#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
小红书对标账号拆解分析器
基于"基础对标+综合对标"方法论，自动化拆解小红书博主的起号路径和爆文策略。
"""

import os
import sys
import json
import time
import re
import asyncio
import subprocess
from datetime import datetime
from collections import Counter
from pathlib import Path
from typing import Optional, List, Dict, Any

# ==================== 配置 ====================

BROWSER_CDP = "http://127.0.0.1:18800"
WHISPER_MODEL = "base"

def find_openclaw_root() -> Optional[Path]:
    current = Path(__file__).resolve().parent
    for _ in range(5):
        if (current / 'config.json').exists() and (current / 'skills').is_dir():
            return current
        if current.parent == current:
            break
        current = current.parent
    home = Path.home() / '.openclaw'
    return home if home.exists() else None

OPENCLAW_ROOT = find_openclaw_root() or Path.home() / '.openclaw'
CONFIG_PATH = OPENCLAW_ROOT / 'config.json'
DEFAULT_OUTPUT_DIR = OPENCLAW_ROOT / "workspace" / "data" / "xhs-account-analyzer"


# ==================== 浏览器数据抓取 ====================

async def extract_profile_notes(page, profile_url, max_scrolls=30):
    """通过 Playwright 抓取博主主页的所有笔记数据"""
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print("❌ 请先安装 Playwright: pip3 install playwright", file=sys.stderr)
        sys.exit(1)

    p = await async_playwright().start()
    browser = await p.chromium.connect_over_cdp(BROWSER_CDP)
    context = browser.contexts[0] if browser.contexts else await browser.new_context()
    pg = context.pages[0] if context.pages else await context.new_page()

    print(f"🌐 正在打开博主主页...", file=sys.stderr)
    try:
        await pg.goto(profile_url, wait_until="domcontentloaded", timeout=30000)
    except Exception as e:
        print(f"  ⚠️ 页面加载超时，继续尝试: {e}", file=sys.stderr)
    await pg.wait_for_timeout(4000)

    # 提取博主基本信息
    profile_info = await extract_xhs_profile_info(pg)

    # 滚动加载更多笔记
    print(f"📜 正在滚动加载笔记列表...", file=sys.stderr)
    all_notes = await scroll_and_collect_notes(pg, max_scrolls)

    await browser.close()
    await p.stop()

    return profile_info, all_notes


async def extract_xhs_profile_info(page):
    """提取小红书博主基本信息"""
    info = {}
    try:
        # 博主名称
        info['name'] = await page.evaluate("""() => {
            // Try multiple selectors for Xiaohongshu
            const selectors = ['.user-name', '[class*="name"]', 'h1', '.nickname'];
            for (const sel of selectors) {
                const el = document.querySelector(sel);
                if (el && el.textContent.trim()) return el.textContent.trim();
            }
            return '';
        }""")

        # 简介
        info['bio'] = await page.evaluate("""() => {
            const el = document.querySelector('[class*="desc"], [class*="info"], .user-desc');
            return el ? el.textContent.trim().substring(0, 300) : '';
        }""")

        # 粉丝、获赞与收藏、笔记数、关注
        info['stats'] = await page.evaluate("""() => {
            const text = document.body.textContent || '';
            // 小红书常见格式: "xxx 粉丝" "xxx 获赞与收藏" "xxx 笔记" "xxx 关注"
            const fansMatch = text.match(/([\d.]+[wW万]?)\s*粉丝/);
            const likesMatch = text.match(/([\d.]+[wW万]?)\s*[获]?赞[与和]?收藏/);
            const notesMatch = text.match(/([\d.]+[wW万]?)\s*笔记/);
            const followMatch = text.match(/([\d.]+[wW万]?)\s*关注/);
            const xhsidMatch = text.match(/小红书号[：:]?\s*([\w-]+)/);
            const ipMatch = text.match(/IP[：:]?\s*([^\s]+)/);
            return {
                fans: fansMatch ? fansMatch[1] : '',
                likes: likesMatch ? likesMatch[1] : '',
                notes: notesMatch ? notesMatch[1] : '',
                follow: followMatch ? followMatch[1] : '',
                xhsid: xhsidMatch ? xhsidMatch[1] : '',
                ip: ipMatch ? ipMatch[1] : ''
            };
        }""")

        # IP 属地（另一种方式）
        if not info['stats'].get('ip'):
            info['stats']['ip'] = await page.evaluate("""() => {
                const el = document.querySelector('[class*="ip"], [class*="location"]');
                return el ? el.textContent.trim().substring(0, 20) : '';
            }""")
    except Exception as e:
        print(f"  ⚠️ 提取博主信息失败: {e}", file=sys.stderr)

    return info


async def scroll_and_collect_notes(page, max_scrolls=30):
    """滚动页面并收集所有笔记卡片数据"""
    seen_ids = set()
    notes = []
    last_count = 0

    for i in range(max_scrolls):
        await page.wait_for_timeout(1500)

        # 提取当前可见的笔记卡片
        new_notes = await page.evaluate("""() => {
            // Xiaohongshu note cards are typically in a grid layout
            // Look for links to note pages
            const cards = Array.from(document.querySelectorAll('a[href*="/explore/"], a[href*="/discovery/item/"]'));
            const notes = [];
            const seen = new Set();

            for (const card of cards) {
                const href = card.href || '';
                // Match /explore/XXXXX or /discovery/item/XXXXX
                const match = href.match(/\/(?:explore|discovery\/item)\/([a-f0-9]{24})/i);
                if (!match) continue;
                const id = match[1].toLowerCase();
                if (seen.has(id)) continue;
                seen.add(id);

                const text = card.textContent || '';
                // Clean up text - remove duplicate content and numbers
                let cleanText = text.replace(/\s+/g, ' ').trim();
                // Remove leading numbers (likes count that might appear before title)
                cleanText = cleanText.replace(/^[\d.]+[wW万]?\s*/, '').trim();

                if (cleanText && cleanText.length > 2) {
                    // Truncate if too long (titles repeat in some layouts)
                    let title = cleanText;
                    // Check for duplicate pattern: "title title"
                    const parts = cleanText.split(' ');
                    if (parts.length > 1) {
                        // Find the unique title (first occurrence)
                        const uniqueParts = [];
                        for (const p of parts) {
                            if (p.length > 1 && !uniqueParts.includes(p)) {
                                uniqueParts.push(p);
                            } else if (p.length > 1 && uniqueParts.includes(p)) {
                                // This might be a duplicate section
                                if (uniqueParts.length > 0) break;
                            }
                        }
                        if (uniqueParts.length > 0) {
                            title = uniqueParts.join(' ');
                        }
                    }
                    if (title.length > 120) title = title.substring(0, 120);

                    notes.push({
                        id,
                        type: 'note',  // default to note, will be refined
                        title: title,
                        likes: 0,
                        collections: 0,
                        comments: 0,
                        href: href
                    });
                }
            }

            return notes;
        }""")

        for n in new_notes:
            if n['id'] not in seen_ids and n.get('id'):
                seen_ids.add(n['id'])
                notes.append(n)

        if len(seen_ids) > last_count:
            last_count = len(seen_ids)
            print(f"  📊 已加载 {last_count} 条笔记...", file=sys.stderr)

        await page.evaluate("window.scrollBy(0, 600)")

    return notes


async def enrich_note_data(page, note):
    """进入笔记详情页，提取完整数据（点赞、收藏、评论、类型）"""
    try:
        note_id = note['id']
        url = f"https://www.xiaohongshu.com/explore/{note_id}"
        await page.goto(url, wait_until="domcontentloaded", timeout=15000)
        await page.wait_for_timeout(2000)

        # 提取详细数据
        detail = await page.evaluate("""() => {
            const text = document.body.textContent || '';

            // 点赞、收藏、评论通常在互动区域
            const likesMatch = text.match(/(\d+)\s*点赞/);
            const collectionsMatch = text.match(/(\d+)\s*收藏/);
            const commentsMatch = text.match(/(\d+)\s*评论/);

            // 检查是否为视频笔记
            const videoEl = document.querySelector('video');
            const isVideo = !!videoEl;

            // 标题
            const titleEl = document.querySelector('h1, .title, [class*="title"]');
            const title = titleEl ? titleEl.textContent.trim() : '';

            // 正文
            const contentEl = document.querySelector('[class*="content"], [class*="desc"], .note-content');
            const content = contentEl ? contentEl.textContent.trim().substring(0, 500) : '';

            return {
                likes: likesMatch ? parseInt(likesMatch[1]) : 0,
                collections: collectionsMatch ? parseInt(collectionsMatch[1]) : 0,
                comments: commentsMatch ? parseInt(commentsMatch[1]) : 0,
                is_video: isVideo,
                title: title,
                content: content
            };
        }""")

        note['likes'] = detail.get('likes', 0) or 0
        note['collections'] = detail.get('collections', 0) or 0
        note['comments'] = detail.get('comments', 0) or 0
        note['is_video'] = detail.get('is_video', False)
        if detail.get('is_video'):
            note['type'] = 'video'
        if detail.get('title'):
            note['title'] = detail['title']
        if detail.get('content'):
            note['content'] = detail['content']

    except Exception as e:
        print(f"  ⚠️ 提取笔记 {note['id']} 详情失败: {e}", file=sys.stderr)

    return note


async def get_browser_page():
    """获取 Playwright page 对象"""
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print("❌ 请先安装 Playwright: pip3 install playwright", file=sys.stderr)
        return None, None

    p = await async_playwright().start()
    browser = await p.chromium.connect_over_cdp(BROWSER_CDP)
    context = browser.contexts[0] if browser.contexts else await browser.new_context()
    page = context.pages[0] if context.pages else await context.new_page()
    return p, page


# ==================== 数据分析 ====================

def analyze_notes(notes: List[Dict], mode: str = 'basic'):
    """分析笔记数据，生成拆解报告"""

    # 按点赞排序
    notes_by_likes = sorted(notes, key=lambda v: v.get('likes', 0), reverse=True)

    report = {
        'total_notes': len(notes),
        'notes': notes_by_likes,
    }

    if not notes:
        return report

    # 基础统计 - 点赞
    all_likes = [v.get('likes', 0) for v in notes]
    report['total_likes'] = sum(all_likes)
    report['avg_likes'] = int(sum(all_likes) / len(all_likes)) if all_likes else 0
    report['median_likes'] = sorted(all_likes)[len(all_likes) // 2] if all_likes else 0
    report['max_likes'] = max(all_likes) if all_likes else 0

    # 基础统计 - 收藏
    all_collections = [v.get('collections', 0) for v in notes]
    report['total_collections'] = sum(all_collections)
    report['avg_collections'] = int(sum(all_collections) / len(all_collections)) if all_collections else 0

    # 基础统计 - 评论
    all_comments = [v.get('comments', 0) for v in notes]
    report['total_comments'] = sum(all_comments)
    report['avg_comments'] = int(sum(all_comments) / len(all_comments)) if all_comments else 0

    # 数据分布
    report['over_100'] = len([l for l in all_likes if l >= 100])
    report['over_1000'] = len([l for l in all_likes if l >= 1000])
    report['over_10000'] = len([l for l in all_likes if l >= 10000])

    # 标题关键词分析
    all_titles = ' '.join([v.get('title', '') for v in notes])
    # 提取话题标签
    hashtags = re.findall(r'#([^#\s]+)', all_titles)
    report['top_hashtags'] = Counter(hashtags).most_common(15)

    # 标题长度分析
    title_lengths = [len(v.get('title', '')) for v in notes if v.get('title')]
    report['avg_title_length'] = int(sum(title_lengths) / len(title_lengths)) if title_lengths else 0

    # 爆款笔记分类
    report['top_10'] = notes_by_likes[:10]

    # 内容类型分布
    video_count = len([n for n in notes if n.get('is_video') or n.get('type') == 'video'])
    image_count = len(notes) - video_count
    report['content_types'] = {
        'image_notes': image_count,
        'video_notes': video_count,
    }

    # 起号路径分析
    if len(notes_by_likes) > 0:
        first_over_100 = None
        first_over_1000 = None
        for v in notes_by_likes:
            if v.get('likes', 0) >= 100 and not first_over_100:
                first_over_100 = v
            if v.get('likes', 0) >= 1000 and not first_over_1000:
                first_over_1000 = v

        report['milestones'] = {
            'top_note': notes_by_likes[0],
            'first_over_100': first_over_100,
            'first_over_1000': first_over_1000,
        }

    # 综合对标额外分析
    if mode == 'comprehensive':
        report['content_directions'] = analyze_content_directions(notes)
        report['engagement_ratio'] = analyze_engagement(notes)

    return report


def analyze_content_directions(notes):
    """分析内容方向"""
    directions = []
    for n in notes:
        title = n.get('title', '')
        hashtags = re.findall(r'#([^#\s]+)', title)
        if hashtags:
            directions.extend(hashtags)
    return Counter(directions).most_common(10)


def analyze_engagement(notes):
    """分析互动情况"""
    total_likes = sum(n.get('likes', 0) for n in notes)
    total_collections = sum(n.get('collections', 0) for n in notes)
    total_comments = sum(n.get('comments', 0) for n in notes)
    return {
        'total_likes': total_likes,
        'total_collections': total_collections,
        'total_comments': total_comments,
        'avg_likes_per_note': int(total_likes / len(notes)) if notes else 0,
        'collection_like_ratio': round(total_collections / total_likes, 2) if total_likes > 0 else 0,
    }


# ==================== 报告生成 ====================

def generate_report(report: Dict, profile_info: Dict, mode: str, output_file: Path):
    """生成 Markdown 格式的分析报告"""

    lines = []
    lines.append(f"# 小红书对标账号拆解报告")
    lines.append("")
    lines.append(f"**生成时间:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"**拆解模式:** {'综合对标' if mode == 'comprehensive' else '基础对标'}")
    lines.append("")

    # 博主信息
    if profile_info:
        lines.append(f"## 👤 博主信息")
        lines.append("")
        if profile_info.get('name'):
            lines.append(f"**昵称:** {profile_info['name']}")
        if profile_info.get('bio'):
            lines.append(f"**简介:** {profile_info['bio']}")
        stats = profile_info.get('stats', {})
        if stats.get('fans'):
            lines.append(f"**粉丝:** {stats['fans']}")
        if stats.get('likes'):
            lines.append(f"**获赞与收藏:** {stats['likes']}")
        if stats.get('notes'):
            lines.append(f"**笔记:** {stats['notes']}")
        if stats.get('follow'):
            lines.append(f"**关注:** {stats['follow']}")
        if stats.get('xhsid'):
            lines.append(f"**小红书号:** {stats['xhsid']}")
        if stats.get('ip'):
            lines.append(f"**IP属地:** {stats['ip']}")
        lines.append("")
        lines.append("---")
        lines.append("")

    # 数据概览
    lines.append(f"## 📊 数据概览")
    lines.append("")
    lines.append(f"- **笔记总数:** {report.get('total_notes', 0)}")
    lines.append(f"- **总获赞:** {report.get('total_likes', 0):,}")
    lines.append(f"- **总收藏:** {report.get('total_collections', 0):,}")
    lines.append(f"- **总评论:** {report.get('total_comments', 0):,}")
    lines.append(f"- **平均点赞:** {report.get('avg_likes', 0):,}")
    lines.append(f"- **平均收藏:** {report.get('avg_collections', 0):,}")
    lines.append(f"- **中位点赞:** {report.get('median_likes', 0):,}")
    lines.append(f"- **最高点赞:** {report.get('max_likes', 0):,}")
    lines.append(f"- **破百赞笔记:** {report.get('over_100', 0)} 篇")
    lines.append(f"- **破千赞笔记:** {report.get('over_1000', 0)} 篇")

    # 内容类型
    ct = report.get('content_types', {})
    if ct:
        lines.append(f"- **图文笔记:** {ct.get('image_notes', 0)} 篇")
        lines.append(f"- **视频笔记:** {ct.get('video_notes', 0)} 篇")
    lines.append("")
    lines.append("---")
    lines.append("")

    # 起号路径分析
    milestones = report.get('milestones', {})
    if milestones:
        lines.append(f"## 🚀 起号路径分析")
        lines.append("")

        if milestones.get('top_note'):
            n = milestones['top_note']
            lines.append(f"### 🏆 最高赞笔记")
            lines.append(f"- **标题:** {n.get('title', '')}")
            lines.append(f"- **点赞:** {n.get('likes', 0):,}")
            lines.append(f"- **收藏:** {n.get('collections', 0):,}")
            lines.append(f"- **评论:** {n.get('comments', 0):,}")
            lines.append(f"- **笔记ID:** {n.get('id', '')}")
            lines.append(f"- **链接:** https://www.xiaohongshu.com/explore/{n.get('id', '')}")
            lines.append("")

        if milestones.get('first_over_100'):
            n = milestones['first_over_100']
            lines.append(f"### 💯 破百赞笔记")
            lines.append(f"- **标题:** {n.get('title', '')}")
            lines.append(f"- **点赞:** {n.get('likes', 0):,}")
            lines.append(f"- **收藏:** {n.get('collections', 0):,}")
            lines.append(f"- **笔记ID:** {n.get('id', '')}")
            lines.append(f"- **链接:** https://www.xiaohongshu.com/explore/{n.get('id', '')}")
            lines.append("")

        if milestones.get('first_over_1000'):
            n = milestones['first_over_1000']
            lines.append(f"### 🔥 破千赞笔记")
            lines.append(f"- **标题:** {n.get('title', '')}")
            lines.append(f"- **点赞:** {n.get('likes', 0):,}")
            lines.append(f"- **收藏:** {n.get('collections', 0):,}")
            lines.append(f"- **笔记ID:** {n.get('id', '')}")
            lines.append(f"- **链接:** https://www.xiaohongshu.com/explore/{n.get('id', '')}")
            lines.append("")

        lines.append("---")
        lines.append("")

    # 爆文 TOP 10
    top_10 = report.get('top_10', [])
    if top_10:
        lines.append(f"## 📈 爆文 TOP 10")
        lines.append("")
        for i, n in enumerate(top_10, 1):
            note_type = "🎬视频" if n.get('is_video') else "📸图文"
            lines.append(f"### {i}. {note_type} {n.get('title', '无标题')}")
            lines.append(f"- 点赞: {n.get('likes', 0):,} | 收藏: {n.get('collections', 0):,} | 评论: {n.get('comments', 0):,}")
            lines.append(f"- 笔记ID: {n.get('id', '')}")
            lines.append(f"- 链接: https://www.xiaohongshu.com/explore/{n.get('id', '')}")
            lines.append("")
        lines.append("---")
        lines.append("")

    # 热门话题标签
    top_hashtags = report.get('top_hashtags', [])
    if top_hashtags:
        lines.append(f"## 🏷️ 热门话题标签")
        lines.append("")
        for tag, count in top_hashtags:
            lines.append(f"- #{tag} ({count} 次)")
        lines.append("")
        lines.append("---")
        lines.append("")

    # 综合对标额外内容
    if mode == 'comprehensive':
        lines.append(f"## 💰 变现路径分析")
        lines.append("")
        lines.append("> ⚠️ 需要人工进一步分析以下内容：")
        lines.append("1. 是否接广告/商单？（查看笔记中的品牌标签和广告标识）")
        lines.append("2. 是否带货？（查看是否挂载商品链接）")
        lines.append("3. 是否引流私域？（查看主页是否留联系方式）")
        lines.append("4. 是否开店？（查看是否有店铺入口）")
        lines.append("")
        lines.append("---")
        lines.append("")

    # 爆款因子分析
    lines.append(f"## 🔍 爆款因子分析")
    lines.append("")
    lines.append("> ⚠️ 以下为自动分析，建议结合人工判断：")
    lines.append("")

    if top_10:
        lines.append(f"### 标题特征")
        # 疑问式
        question_count = sum(1 for n in top_10 if '？' in n.get('title', '') or '?' in n.get('title', ''))
        if question_count > len(top_10) * 0.3:
            lines.append(f"- ✅ **疑问式标题**: {question_count}/{len(top_10)} 使用问号，引发好奇心")

        # 感叹式
        exclaim_count = sum(1 for n in top_10 if '！' in n.get('title', '') or '!' in n.get('title', ''))
        if exclaim_count > len(top_10) * 0.3:
            lines.append(f"- ✅ **感叹式标题**: {exclaim_count}/{len(top_10)} 使用感叹号，增强情绪")

        # 数字型
        num_count = sum(1 for n in top_10 if re.search(r'\d+', n.get('title', '')))
        if num_count > len(top_10) * 0.3:
            lines.append(f"- ✅ **数字型标题**: {num_count}/{len(top_10)} 包含具体数字")

        # emoji 使用
        emoji_count = sum(1 for n in top_10 if any(ord(c) > 0x1F000 or 0x2600 <= ord(c) <= 0x27BF for c in n.get('title', '')))
        if emoji_count > len(top_10) * 0.3:
            lines.append(f"- ✅ **Emoji标题**: {emoji_count}/{len(top_10)} 使用Emoji增强视觉吸引力")

        lines.append("")

    lines.append(f"### 内容方向")
    if top_hashtags:
        for tag, count in top_hashtags[:5]:
            lines.append(f"- **#{tag}**: 出现 {count} 次")
    lines.append("")

    # 收藏点赞比分析
    engagement = report.get('engagement_ratio', {})
    if engagement:
        ratio = engagement.get('collection_like_ratio', 0)
        if ratio > 0:
            lines.append(f"### 收藏/点赞比")
            lines.append(f"- **比例:** {ratio:.2f}")
            if ratio > 1.0:
                lines.append(f"- 💡 收藏数大于点赞数，说明内容**实用性强**，用户倾向于收藏备用")
            elif ratio > 0.5:
                lines.append(f"- 💡 收藏/点赞比例正常，内容有一定的实用价值")
            else:
                lines.append(f"- 💡 收藏比例偏低，可能需要增加**干货/实用**内容")
            lines.append("")

    lines.append("---")
    lines.append("")
    lines.append(f"*报告由 XHS Account Analyzer 自动生成*")

    output_file.parent.mkdir(parents=True, exist_ok=True)
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))

    return output_file


# ==================== 视频笔记转写 ====================

def download_video(url, path):
    import requests
    headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://www.xiaohongshu.com/"}
    with requests.get(url, headers=headers, stream=True, timeout=120) as r:
        r.raise_for_status()
        with open(path, "wb") as f:
            for chunk in r.iter_content(chunk_size=8192):
                f.write(chunk)
    size = os.path.getsize(path)
    if size < 1000:
        raise ValueError(f"文件太小({size}B)")
    return path


def extract_audio(video_path, audio_path):
    cmd = ['ffmpeg', '-i', str(video_path), '-vn', '-acodec', 'libmp3lame', '-y', str(audio_path)]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    return os.path.exists(audio_path) and os.path.getsize(audio_path) > 0, result.stderr[:300] if result.returncode != 0 else ""


def transcribe_audio(audio_path):
    import whisper
    model = whisper.load_model(WHISPER_MODEL)
    result = model.transcribe(audio=str(audio_path), language='zh', initial_prompt="以下是普通话的句子。")
    return result['text'].strip()


async def transcribe_top_notes(page, notes, top_n, report_file):
    """转写点赞最高的前 N 条视频笔记"""
    import tempfile

    if top_n <= 0:
        return

    print(f"\n🎤 开始转写 Top {top_n} 视频笔记文案...", file=sys.stderr)

    lines = ["\n---\n", f"\n## 🎙️ 视频笔记文案转写 (Top {top_n})\n", ""]

    success = 0
    for i, note in enumerate(notes[:top_n]):
        # 跳过图文笔记
        if not note.get('is_video') and note.get('type') != 'video':
            print(f"\n  [{i+1}/{top_n}] 📸 跳过图文笔记: {note.get('title', '')[:40]}...", file=sys.stderr)
            continue

        print(f"\n  [{i+1}/{top_n}] 🎬 👍{note.get('likes', 0)} {note.get('title', '')[:40]}...", file=sys.stderr)

        try:
            note_id = note['id']
            video_url = await extract_xhs_video_url(page, note_id)
            if not video_url:
                print(f"  ✗ 无法获取视频链接", file=sys.stderr)
                continue

            video_path = Path(tempfile.gettempdir()) / f"xhs_{note_id}.mp4"
            audio_path = Path(tempfile.gettempdir()) / f"xhs_{note_id}.mp3"

            print(f"  [1/3] 下载视频...", file=sys.stderr)
            download_video(video_url, video_path)

            print(f"  [2/3] 提取音频...", file=sys.stderr)
            ok, err = extract_audio(video_path, audio_path)
            if not ok:
                raise Exception(f"音频提取失败: {err}")

            print(f"  [3/3] Whisper 转写...", file=sys.stderr)
            transcription = transcribe_audio(audio_path)

            for p in [video_path, audio_path]:
                if p.exists():
                    p.unlink()

            lines.append(f"### {success+1}. {note.get('title', '')}")
            lines.append(f"- 点赞: {note.get('likes', 0):,} | 收藏: {note.get('collections', 0):,}")
            lines.append(f"- 链接: https://www.xiaohongshu.com/explore/{note_id}")
            lines.append("")
            lines.append(f"**文案:**")
            lines.append("")
            lines.append(transcription)
            lines.append("")
            lines.append("---")
            lines.append("")

            print(f"  ✅ 完成 ({len(transcription)} 字)", file=sys.stderr)
            success += 1

        except Exception as e:
            print(f"  ❌ 失败: {e}", file=sys.stderr)

    with open(report_file, 'a', encoding='utf-8') as f:
        f.write('\n'.join(lines))

    print(f"\n🎉 转写完成: {success}/{top_n}", file=sys.stderr)


async def extract_xhs_video_url(page, note_id):
    """从小红书笔记页面提取视频直链"""
    try:
        await page.goto(f"https://www.xiaohongshu.com/explore/{note_id}", wait_until="domcontentloaded", timeout=15000)
        await page.wait_for_timeout(3000)

        video_url = await page.evaluate("""() => {
            const v = document.querySelector('video');
            if (v && v.src) return v.src;
            // Try source elements
            const sources = document.querySelectorAll('video source');
            for (const s of sources) {
                if (s.src) return s.src;
            }
            return null;
        }""")
        return video_url
    except Exception as e:
        print(f"  ⚠️ 提取视频链接失败: {e}", file=sys.stderr)
        return None


# ==================== 主流程 ====================

def main():
    import argparse

    parser = argparse.ArgumentParser(description="小红书对标账号拆解分析器")
    parser.add_argument("--profile-url", required=True, help="博主主页URL")
    parser.add_argument("--mode", choices=['basic', 'comprehensive'], default='basic', help="拆解模式")
    parser.add_argument("--transcribe-top", type=int, default=0, help="转写点赞最高的前N条视频笔记")
    parser.add_argument("--output-file", help="输出报告路径")
    parser.add_argument("--max-scrolls", type=int, default=30, help="最大滚动次数（加载笔记数）")
    args = parser.parse_args()

    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    output_file = Path(args.output_file) if args.output_file else DEFAULT_OUTPUT_DIR / f"{timestamp}_xhs_analysis.md"

    print(f"🔍 小红书对标账号拆解分析器")
    print(f"📋 模式: {'综合对标' if args.mode == 'comprehensive' else '基础对标'}")
    print(f"🔗 目标: {args.profile_url}")
    print()

    # 浏览器抓取
    print("[1/3] 正在抓取博主主页数据...", file=sys.stderr)
    profile_info, notes = asyncio.run(extract_profile_notes(None, args.profile_url, args.max_scrolls))

    if not notes:
        print("❌ 未能抓取到笔记数据", file=sys.stderr)
        sys.exit(1)

    print(f"  ✅ 抓取到 {len(notes)} 条笔记", file=sys.stderr)

    # 分析
    print(f"\n[2/3] 正在分析数据...", file=sys.stderr)
    report = analyze_notes(notes, args.mode)

    # 生成报告
    print(f"[3/3] 正在生成报告...", file=sys.stderr)
    output_file = generate_report(report, profile_info, args.mode, output_file)
    print(f"  ✅ 报告已保存: {output_file}", file=sys.stderr)

    # 如果需要转写
    if args.transcribe_top > 0:
        print(f"\n[4/4] 正在转写 Top {args.transcribe_top} 视频笔记...", file=sys.stderr)

        async def run_transcribe():
            p, page = await get_browser_page()
            await transcribe_top_notes(page, report.get('notes', []), args.transcribe_top, output_file)
            await p.stop()

        asyncio.run(run_transcribe())

    print(f"\n🎉 分析完成!")
    print(f"📁 报告: {output_file}")
    print(f"ANALYSIS_PATH:{output_file}")


if __name__ == "__main__":
    main()
