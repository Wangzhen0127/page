# MeaXure Converter

将 **Sketch MeaXure Web Export** 的 `index.html` 转成：

- 原生 **HTML**（**每个画板一页**，可编辑 DOM）
- 原生 **微信小程序**（每个画板一个 page）

## 重要：多画板

MeaXure 一个 `index.html` 里常有多个 artboard（本仓库 `关怀版/` 有 **19** 个）。  
默认会 **全部导出**，不再合成成一个页面。

## 默认模式：`hybrid`（绝对定位代码还原）

按 MeaXure 原始 `rect` + 图层绘制顺序生成可编辑 HTML/CSS：

```html
<div class="page" style="position:relative; width:750px; height:...">
  <div class="shape" style="position:absolute; left:...; top:..."></div>
  <div class="text"  style="position:absolute; left:...; top:...">文案</div>
  <img class="asset" style="position:absolute; left:...; top:..." />
</div>
```

| 图层类型 | 输出 |
|------|------|
| text | 可编辑文本（`position:absolute`，保留字号/行高/字距） |
| 纯色 / 圆角 / 椭圆 / 渐变 | CSS |
| 已有 `exportable` 切图 | 原始 `assets` 图片 |
| 小型复杂路径 / 组合形状 | **局部** preview 裁图（≤120px 且面积 < 画板 1%） |
| 大背景 / 卡片 | **禁止**裁图，必须 CSS |
| 整页 preview | **禁止**进入最终 DOM |

当前阶段**不做** flex/grid 推断、不做空间父子恢复，避免错位。  
组件化 / flex 转换属于后续阶段，需在截图 diff 通过后再做。

可选 `--mode fidelity`：整页 preview 快照（像素对照用）。

## 用法

```bash
pip install -r requirements.txt

# 列出全部画板 + preview 是否齐全
python -m meaxure_converter 关怀版/index.html --list

# 默认：全部画板 → HTML + 小程序（绝对定位可编辑）
python -m meaxure_converter 关怀版/index.html -o output/guanhuai

# 只转某一个画板
python -m meaxure_converter 关怀版/index.html -o out -a 2

# 只要 HTML / 只要小程序
python -m meaxure_converter 关怀版/index.html -o out -f html
python -m meaxure_converter 关怀版/index.html -o out -f miniprogram

# 整页 preview 对照模式
python -m meaxure_converter 关怀版/index.html -o out --mode fidelity
```

### 输出结构

```text
output/guanhuai/
  html/
    index.html              # 画板画廊入口
    pages/<slug>.html       # 每个画板一页（可编辑 DOM）
    assets/                 # 原始切图 + 少量局部 crop
  miniprogram/
    app.js / app.json / ...
    pages/<slug>/
    assets/...
```

打开 `output/guanhuai/html/index.html` 可浏览全部页面。

## 输入目录要求

```text
关怀版/
  index.html
  assets/          # 切图（有则优先复用）
  preview/         # 各画板 @2x 预览（局部 crop / 渐变采样用）
  links/           # 可选
  proto.html
```

## License

MIT
