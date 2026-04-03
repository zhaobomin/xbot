# Crew Media Support 使用指南

## 概述

xbot crew 现已支持 **media 字段**，允许任务直接引用图片等视觉资源，实现 Vision 能力分析。

## 使用场景

- 📊 PPT Review：直接分析幻灯片图片
- 🖼️ 文档 OCR：提取图片中的文字内容
- 📈 图表分析：解读数据可视化内容
- 🔍 图像识别：分析图片中的对象、场景等

## 配置方式

### 1. 基本用法

```yaml
tasks:
  - name: analyze_slide
    description: 分析幻灯片内容
    agent: slide_analyst
    media:
      - slides/page_01.png
    media_mode: vision
```

### 2. 多个媒体文件

```yaml
tasks:
  - name: compare_slides
    description: 对比多页内容
    agent: analyst
    media:
      - slides/page_01.png
      - slides/page_02.png
      - slides/page_03.png
```

### 3. 通配符支持

```yaml
tasks:
  - name: batch_analyze
    description: 批量分析所有幻灯片
    agent: batch_analyzer
    media:
      - slides/*.png
```

## media_mode 选项

| 模式 | 说明 |
|------|------|
| `auto` | 默认，有 media 自动使用 vision |
| `vision` | 强制使用 vision 模式（要求 model 支持） |
| `text_only` | 仅使用文本，忽略 media |

## 完整示例

```yaml
name: ppt_review
description: PPT Review with Vision Support

process: sequential

agents:
  slide_analyzer:
    name: slide_analyzer
    description: Slide content analyzer
    goal: Extract content from slide images
    model: kimi-k2.5  # 确保使用支持 vision 的模型

tasks:
  - name: extract_content
    description: |
      Analyze the slide image and extract:
      1. Title
      2. Key points
      3. Charts/diagrams description
    agent: slide_analyzer
    media:
      - slides/page_01.png
    media_mode: vision
    expected_output: Structured slide content
```

## 技术实现

### 修改的文件

1. `xbot/agent/crew/models.py` - TaskDefinition 新增 `media` 和 `media_mode` 字段
2. `xbot/agent/crew/context.py` - CrewExecutionContext 支持 media 传递
3. `xbot/agent/crew/agent_pool.py` - AgentPool 方法支持 media 参数
4. `xbot/agent/crew/process.py` - Process 层传递 media 到 backend

### 向后兼容

✅ **完全向后兼容**

- 旧配置（无 media 字段）继续工作
- 所有 media 相关字段都是 Optional
- 新功能默认关闭（media=None, media_mode="auto"）

## 测试

运行测试验证：

```bash
cd /Users/zhaobomin/Documents/projects/thirdpart/xbot
python -m pytest tests/agent/crew/test_media_support.py -v
```

## 注意事项

1. **模型要求**：确保 agent 配置的 model 支持 vision（如 kimi-k2.5）
2. **文件路径**：支持相对路径（相对于 workspace）和绝对路径
3. **通配符**：支持 `*` 和 `?` 通配符
4. **超时**：vision 任务可能需要更长的 timeout

## 故障排查

### media 文件找不到

确保文件路径正确，相对于 workspace 配置：

```yaml
workspace: ./my_project  # 基础目录
# media 路径: ./my_project/slides/page_01.png
```

### model 不支持 vision

检查 model 配置：

```yaml
agents:
  my_agent:
    model: kimi-k2.5  # 或其他支持 vision 的模型
```

## 示例文件

参考完整示例：`xbot/agent/crew/examples/ppt_review_demo.yaml`
