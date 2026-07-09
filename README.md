# MeaXure Converter

将 **Sketch MeaXure Web Export** 导出的 `index.html` 转成：

- 原生 **HTML** 页面
- 原生 **微信小程序** 页面（WXML / WXSS / JS / JSON）

## 设计原则

| 类型 | 定位方式 |
|------|----------|
| 结构层（文本、矩形、椭圆等） | 画板为 **column flex**；每层 `margin-top = y - prev_bottom`（可负）、`margin-left = x`，用弹性布局还原绝对坐标 |
| 切片素材（slice / exportable） | **原始坐标 absolute**（相对画板） |
| 复杂矢量 / 位图占位 | 从 MeaXure `preview/@2x` 按 rect **裁切**补全（CSS 无法表达的路径） |

目标：视觉位置尽量 1:1；结构层不用 absolute，素材保留原始定位。

## 输入要求

MeaXure 导出目录大致如下：

```text
your-export/
  index.html          # 内含 `let data = {...}`
  assets/             # 切片图片（webp/png）
  preview/            # 可选预览图
```

本工具只依赖 `index.html` 里的 `data`；若旁边有 `assets/`，会自动拷贝到输出目录。

## 安装

```bash
cd /path/to/page
pip install -e .
# 或直接：
python -m meaxure_converter --help
```

## 用法

```bash
# 同时输出 HTML + 小程序
python -m meaxure_converter path/to/index.html -o output

# 只出 HTML
python -m meaxure_converter path/to/index.html -o output -f html

# 只出微信小程序
python -m meaxure_converter path/to/index.html -o output -f miniprogram

# 列出画板
python -m meaxure_converter path/to/index.html --list

# 指定画板下标
python -m meaxure_converter path/to/index.html -a 0 -f both
```

### 输出结构

```text
output/
  html/
    index.html
    assets/...
  miniprogram/
    app.js / app.json / app.wxss
    project.config.json
    pages/index/index.{wxml,wxss,js,json}
    assets/...
```

小程序可直接用微信开发者工具打开 `output/miniprogram`。

## 示例

仓库内 `samples/meaxure-index.html` 为精简样例（来自 MeaXure 导出数据）：

```bash
python -m meaxure_converter samples/meaxure-index.html -o output
python -m unittest discover -s tests -v
```

> 样例未附带真实 `assets/` 图片，转换后图片路径会保留，需把原导出目录的 `assets/` 放到 `samples/assets` 或输出目录中。

## 模块说明

```text
meaxure_converter/
  parser.py           # 解析 let data = {...}
  layout.py           # 分组树 + flex 行聚类 + 素材绝对定位
  styles.py           # fills / borders / shadows / text → CSS/WXSS
  html_gen.py         # 生成原生 HTML
  miniprogram_gen.py  # 生成微信小程序
  cli.py              # 命令行入口
```

## 限制与说明

- MeaXure 图层列表是扁平的，父子关系靠 **group 包围盒** 推断，复杂蒙版/布尔运算无法 100% 还原。
- 复杂矢量图标若已导出为 slice，会优先用图片素材，并抑制被切片覆盖的矢量层。
- 渐变描边等能力按近似色处理。
- 单位：HTML 用 `px`（按设计稿 750）；小程序用 `rpx`（750 设计宽 1:1）。
- 字体依赖运行环境（如 PingFang SC）；小程序端需自行配置字体或回退。

## License

MIT
