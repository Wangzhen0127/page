# MeaXure Converter

将 **Sketch MeaXure Web Export** 的 `index.html` 转成：

- 原生 **HTML**（**每个画板一页**，可编辑 DOM）
- 原生 **微信小程序**（每个画板一个 page）

## 重要：多画板

MeaXure 一个 `index.html` 里常有多个 artboard（本仓库 `关怀版/` 有 **19** 个）。  
默认会 **全部导出**，不再合成成一个页面。

## 默认模式：`hybrid`（可编辑还原）

按图层位置、颜色、文字重建 HTML/CSS，而不是整页嵌套 preview 图。

| 图层类型 | 输出 |
|------|------|
| text | 可编辑文本节点 |
| 纯色 / 圆角 / 椭圆 / 渐变 | CSS |
| 已有 `exportable` 切图 | 原始 `assets` 图片 |
| 小型复杂路径 / 组合形状 | **局部** preview 裁图（≤120px，且无前景文字覆盖） |
| 大背景 / 卡片 | **禁止**裁图，必须 CSS |
| 整页 preview | **仅作参考**，不进入最终 DOM |

布局策略：

1. 用矩形包含关系恢复卡片 / section 容器  
2. 无重叠兄弟 → `flex` column / row / `grid`  
3. 重叠模块 → 局部 `position: absolute` overlay  
4. slice 图标相对所属模块定位  

可选 `--mode fidelity`：整页 preview 快照（像素对照用）。  
`--mode flex`：兼容旧入口，内部同样走 hybrid 树构建。

## 用法

```bash
pip install -r requirements.txt

# 列出全部画板 + preview 是否齐全
python -m meaxure_converter 关怀版/index.html --list

# 默认：全部画板 → HTML + 小程序（hybrid 可编辑）
python -m meaxure_converter 关怀版/index.html -o output/guanhuai

# 只转某一个画板
python -m meaxure_converter 关怀版/index.html -o out -a 1

# 只要 HTML / 只要小程序
python -m meaxure_converter 关怀版/index.html -o out -f html
python -m meaxure_converter 关怀版/index.html -o out -f miniprogram

# 整页 preview 对照模式
python -m meaxure_converter 关怀版/index.html -o out --mode fidelity
```

### 输出结构（hybrid）

```text
output/guanhuai/
  html/
    index.html              # 画板画廊入口
    pages/<slug>.html       # 每个画板一页（可编辑 DOM）
    assets/                 # 原始切图 + 少量局部 crop
  miniprogram/
    app.js / app.json / ...
    pages/<slug>/           # 每个画板一个小程序页
    assets/...
```

打开 `output/guanhuai/html/index.html` 可浏览全部页面。

## 输入目录要求

```text
关怀版/
  index.html
  assets/          # 切图（有则优先复用）
  preview/         # 各画板 @2x 预览（局部 crop / 渐变采样用）
  links/           # 可选，MeaXure 页面跳转
  proto.html
```

## License

MIT
