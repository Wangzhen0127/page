# MeaXure Converter

将 **Sketch MeaXure Web Export** 的 `index.html` 转成：

- 原生 **HTML**（**每个画板一页**）
- 原生 **微信小程序**（每个画板一个 page）

## 重要：多画板

MeaXure 一个 `index.html` 里常有多个 artboard（本仓库 `关怀版/` 有 **19** 个）。  
默认会 **全部导出**，不再合成成一个页面。

`links/*.html` 对应 MeaXure 的 `#index` 跳转；转换后用画板 `slug` 作为独立页面名。

## 默认模式：`fidelity`（1:1）

视觉以 MeaXure `preview/**/@2x.png` 为准，整页铺满，保证与设计稿像素一致。  
同时拷贝 `assets/` 切图，方便开发替换/拆分。

| 资源 | 用途 |
|------|------|
| `preview/@2x.png` | 页面视觉 1:1 底图 |
| `assets/*` 切图 | 开发素材；难还原处直接用切图 |
| `links/*.html` | 画板索引参考（`#N`） |

可选 `--mode flex`：用图层 flex+margin 重建（适合二次改版，还原度低于 fidelity）。

## 用法

```bash
pip install -r requirements.txt

# 列出全部画板 + preview 是否齐全
python -m meaxure_converter 关怀版/index.html --list

# 默认：全部画板 → HTML + 小程序（fidelity 1:1）
python -m meaxure_converter 关怀版/index.html -o output/guanhuai

# 只转某一个画板
python -m meaxure_converter 关怀版/index.html -o out -a 1

# 只要 HTML / 只要小程序
python -m meaxure_converter 关怀版/index.html -o out -f html
python -m meaxure_converter 关怀版/index.html -o out -f miniprogram

# 旧的 flex 重建模式
python -m meaxure_converter 关怀版/index.html -o out --mode flex -a 0
```

### 输出结构（fidelity）

```text
output/guanhuai/
  html/
    index.html              # 画板画廊入口
    pages/<slug>.html       # 每个画板一页
    assets/
      preview_<slug>@2x.png # 该页 1:1 预览图
      ...切图...
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
  assets/          # 切图
  preview/         # 各画板 @2x 预览（fidelity 必需）
  links/           # 可选，MeaXure 页面跳转
  proto.html
```

## License

MIT
