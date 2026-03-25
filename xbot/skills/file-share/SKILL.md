---
name: file-share
type: python
description: Start a temporary HTTP server to share files with download links. Use when user wants to download PDFs, images, Markdown files, or any generated content.
---

# File Share

Start a temporary HTTP server to generate downloadable links for files.

## When to Use

- User says "生成 PDF/图片，发给我下载链接"
- User says "帮我启动服务器下载文件"
- User says "我要下载这个文件"
- Any scenario where user needs to download generated content

## How It Works

1. Generate/save file to a temporary directory
2. Start a lightweight HTTP server on a random port
3. Return a downloadable URL to the user
4. Server auto-closes after timeout (default 10 minutes)

## Usage

### Basic

```
# After generating a file, call the share tool
file_share(file_path="/path/to/report.pdf")
# Returns: http://121.40.69.126:18080/report.pdf
```

### Multiple Files

```
file_share(file_path="/path/to/folder/")
# Returns: http://121.40.69.126:18080/
```

### With Custom Timeout

```
file_share(file_path="/path/to/file.pdf", timeout_minutes=30)
```

## Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| file_path | str | required | Absolute path to file or directory |
| timeout_minutes | int | 10 | Minutes before server auto-stops |
| port | int | 0 (random) | Specific port, 0 for random |

## Server Details

- **Base URL**: http://121.40.69.126:{port}
- **Public IP**: 121.40.69.126
- **Random Port Range**: 18000-18999
- **Max Timeout**: 60 minutes
- **Security**: Only exposes specified directory
- **Encoding**: UTF-8 for text files (md, txt, html, json, etc.)

## Example Flow

**User**: "帮我生成一个PDF报告，发下载链接给我"

**Agent**:
1. Generate PDF to `/tmp/xbot-share/report.pdf`
2. Call `file_share(file_path="/tmp/xbot-share/report.pdf")`
3. Return to user:
   ```
   📥 文件已就绪，请点击下载：
   http://121.40.69.126:18234/report.pdf

   ⏰ 链接 10 分钟后失效
   ```

## Important Notes

- Server runs in background as a daemon process
- Each call starts a new server instance
- Old servers are tracked and cleaned up on timeout
- For sensitive files, consider adding token authentication
- The public IP (121.40.69.126) is accessible from anywhere

## Cleanup

Servers auto-stop after timeout. To manually stop all file-share servers:

```bash
pkill -f "xbot-file-share"
```