# XHS Account Analyzer - 小红书对标账号拆解分析器

自动化拆解小红书对标账号，提取爆文因子、分析起号路径、识别变现模式。

## 功能

- 自动抓取博主主页所有笔记数据（标题、点赞、收藏、评论）
- 数据统计分析（平均/中位/最高点赞，破百赞/千赞数量）
- 起号路径分析（最高赞、破百赞、破千赞笔记识别）
- 热门话题标签提取
- 爆款因子分析（疑问式/感叹式/数字型/Emoji标题检测）
- 视频笔记文案转写（可选，基于 Whisper）
- 收藏/点赞比分析（判断内容实用性）
- 一键生成 Markdown 分析报告

## 用法

### 基础对标

```bash
python3 scripts/analyze.py \
  --profile-url "https://www.xiaohongshu.com/user/profile/XXXXX" \
  --mode basic
```

### 综合对标

```bash
python3 scripts/analyze.py \
  --profile-url "https://www.xiaohongshu.com/user/profile/XXXXX" \
  --mode comprehensive \
  --transcribe-top 5
```

## 参数

| 参数 | 说明 |
|------|------|
| `--profile-url` | 博主主页URL（必填） |
| `--mode` | `basic` 或 `comprehensive`，默认 basic |
| `--transcribe-top` | 转写点赞最高的前N条视频笔记 |
| `--output-file` | 自定义输出报告路径 |
| `--max-scrolls` | 最大滚动次数，默认30 |

## 前置条件

- Playwright (`pip install playwright`)
- ffmpeg（可选，视频笔记转写）
- OpenAI Whisper（可选，视频笔记转写）
