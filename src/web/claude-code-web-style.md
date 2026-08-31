# Claude Code 网页视觉风格文档（CodeAgent Web 改版参考）

> 状态：学习稿，供后续只改视觉层落地。  
> 约束：**页面布局与功能保持不变**；本文件只定义字体、配色、图标、表面与控件气质。  
> 日期：2026-08-31

---

## 1. 研究范围与结论摘要

### 1.1 研究对象

| 表面 | URL / 来源 | 与本改版的关系 |
|------|------------|----------------|
| Claude Code 产品营销页 | [claude.com/product/claude-code](https://claude.com/product/claude-code) | 用户截图主参考（「Think fast, build faster」、产品 Tab、右侧功能卡） |
| Claude 统一营销站 | [claude.com](https://claude.com)、overview / pricing | 与 Claude Code 同属 Anthropic 暖羊皮纸编辑体系 |
| Claude Code on the web | [claude.ai/code](https://claude.ai/code) | 产品形态（侧栏会话、中央对话、底部 composer）；**结构映射可用，token 以营销站公开观测为准** |
| 独立设计系统整理 | oh-my-design / webdesignhot / Refero 等对 Claude·Anthropic 的实测 token | 补全色板、圆角、按钮角色 |

### 1.2 一句话气质

**「纸上的墨迹」**：暖色羊皮纸画布 + 近黑正文 + 衬线标题 + 无衬线控件 + 极少出现的陶土橙点缀。  
反差于冷灰 SaaS、霓虹暗色、紫粉渐变。主行动默认是**近黑矩形**，不是彩色渐变按钮。

### 1.3 与 CodeAgent 现状的主要差异（仅视觉）

| 维度 | CodeAgent 现状 | Claude Code 网页风格 |
|------|----------------|----------------------|
| 画布 | 冷灰 `#f4f5f8` | 暖羊皮纸 `#f5f4ed` / `#f8f8f6` |
| 强调色 | 紫→粉渐变 `#7b5cff → #ff6bcb` | 陶土橙 `#c96442`～`#cd6f47`（低频） |
| 主 CTA | 渐变填充 | 近黑 `#121212` / `#141413` |
| 字体 | 全站 Manrope | 衬线标题 + 无衬线 UI |
| 阴影 | 偏重、带紫调 | 发丝边 / 极轻暖阴影，或 ring（`0 0 0 1px`） |
| 图标 | 常带渐变描边 | 细线单色 outline |

---

## 2. 设计原则（落地时必须遵守）

1. **暖中性优先**：所有灰都带黄褐底，禁止冷蓝灰、冷紫灰作为主色。  
2. **衬线只做「声音」**：大标题 / 问候语 / 区块标题用衬线；导航、按钮、表单、时间线用无衬线。  
3. **陶土橙是签名，不是洪水**：用于品牌标记、极少数强调按钮、链接 hover、装饰星点；不要铺满卡片、进度条、头像。  
4. **主 CTA 用近黑**：发送、确认、主行动用黑底浅字；次要用描边或透明底。  
5. **用表面色分层，少用阴影**：画布 → 暖沙 → 白卡片；边框用奶油发丝线。  
6. **不改结构**：侧栏宽度、网格、聊天区、composer、右侧面板的 DOM/功能逻辑一律不动。

---

## 3. 色板（推荐落地 token）

以下取公开观测中的共识值，并收敛成一套可写进 `:root` 的语义 token。

### 3.1 核心色

| Token | Hex | 用途 |
|-------|-----|------|
| `--canvas` | `#f5f4ed` | 页面 / 侧栏底（羊皮纸） |
| `--canvas-soft` | `#f8f8f6` | 略亮的模块底（可选） |
| `--surface` | `#ffffff` | 卡片、输入框、浮层 |
| `--surface-warm` | `#f3f0ea` | 暖色嵌套块、引用带 |
| `--surface-sand` | `#e8e6dc` | 选中底、toggle 轨、浅填充 |
| `--text` | `#141413` | 主文字、图标默认色 |
| `--text-secondary` | `#5e5d59` | 次要说明、未选中导航 |
| `--text-muted` | `#87867f` | 更弱的 caption / 时间戳 |
| `--text-faint` | `#b0aea5` | 最低优先级标签 |
| `--line` | `#f0eee6` | 默认分割线 / 卡片边 |
| `--line-strong` | `rgba(18,18,18,0.18)` | 描边按钮、输入框边 |
| `--accent` | `#c96442` | 品牌陶土橙（签名色） |
| `--accent-hover` | `#a85530` | 陶土橙按下 / 深态 |
| `--accent-soft` | `#f3e7df` | 陶土浅底（badge） |
| `--cta` | `#121212` | 主按钮底 |
| `--cta-text` | `#f8f8f6` | 主按钮字 |
| `--cta-hover` | `#2a2a2a` | 主按钮 hover |

备选观测值（同源风格，落地时二选一保持一致即可）：

- 画布也见 `#f8f8f6`、`#f0eee6`  
- 品牌橙也见 `#cd6f47`、`#d97757`  
- **推荐统一**：画布 `#f5f4ed`，强调 `#c96442`，CTA `#121212`

### 3.2 语义色（暖调，勿用冷蓝告警）

| Token | Hex | 用途 |
|-------|-----|------|
| `--success` | `#5a8c5a` | 成功 / 完成 |
| `--success-soft` | `#e6efe2` | 成功浅底 |
| `--warning` | `#b58a3c` | 警告 |
| `--warning-soft` | `#fbf3df` | 警告浅底 |
| `--danger` | `#b54a3c` | 错误 / 危险 |
| `--danger-soft` | `#fbe8e3` | 错误浅底 |
| `--info` | `#5a708c` | 信息（少用） |
| `--highlight` | `#fbe8d6` | 行内高亮浅洗 |

### 3.3 明确禁止带回的旧色

- 紫粉渐变：`#7b5cff`、`#ff6bcb` 及一切 `linear-gradient(...purple...)`  
- 冷灰画布：`#f4f5f8`、偏蓝的 `#eceef3` 作为主线色  
- 作为主强调的霓虹、亮蓝、饱和品红  

---

## 4. 字体

### 4.1 官方字体（不可直接商用拷贝）

Claude / Anthropic 网页实测加载：

- **Anthropic Serif**：展示标题（英雄文案「Think fast, build faster」等）  
- **Anthropic Sans**：导航、正文 UI、按钮、Tab  
- **Anthropic Mono** / JetBrains Mono：代码（营销页出现较少）

二者为专有字体，**落地时不得声称使用 Anthropic 字体名冒充**；用开源近似栈。

### 4.2 CodeAgent 推荐开源替代栈

```css
--font-display: "Source Serif 4", "Noto Serif SC", Georgia, "Songti SC", serif;
--font-ui: "IBM Plex Sans", "Noto Sans SC", system-ui, "Segoe UI", sans-serif;
--font-mono: "IBM Plex Mono", "JetBrains Mono", ui-monospace, Consolas, monospace;
```

说明：

- 中文界面需同时挂 **Noto Serif SC / Noto Sans SC**，避免标题掉回默认宋体过粗。  
- Display 字重宜 **400–500**（大标题可更轻），避免 800 喊话感。  
- UI 字重以 **400 / 500** 为主，最重约 **600**，少用 700+。

### 4.3 字号角色（产品 UI 压缩版）

营销站英雄字可达 56–64px；**应用内**按现有布局缩放，只迁移「角色」不照搬营销尺寸：

| 角色 | 字体 | 建议 | 用在 CodeAgent |
|------|------|------|----------------|
| Display / Hello | display | ~28–40px / 400–500 / lh ~1.1 | `.hello` 问候标题 |
| Section title | display 或 ui 500 | 18–22px | 区块标题 |
| Body | ui | 15–16px / 400 / lh 1.5 | 消息、说明 |
| Control | ui | 14–15px / 400–500 | 按钮、输入、导航 |
| Caption / Label | ui | 11–12px / 500 / 略增 tracking | `RECENT CHATS` 等 |
| Code | mono | 13–14px | 代码块、diff |

---

## 5. 图标风格

1. **线型（outline）为主**，几乎无实心填充块。  
2. **线宽一致**：约 1.5–2px（`stroke-width="1.75"` 一类），圆角线帽 `round`。  
3. **颜色**：默认 `currentColor` → `--text` 或 `--text-secondary`；激活可略深，**不要**给 SVG 套紫粉渐变。  
4. **品牌点缀**：仅 logo / 极少数装饰可用 `--accent` 陶土橙（Claude 星形标记同理）。  
5. **尺寸**：导航 / 工具栏约 16–18px；品牌 mark 约 20–24px。  
6. **禁止**：emoji 当主图标、彩色多填充插画当控件图标、粗描边+渐变描边。

---

## 6. 形状、边框与深度

### 6.1 圆角

| Token | 值 | 用途 |
|-------|-----|------|
| `--radius-sm` | `6px` | 输入、小芯片 |
| `--radius-md` | `8px` | 按钮 |
| `--radius-lg` | `12px` | Tab、导航项、中卡 |
| `--radius-xl` | `16–24px` | 大卡片、composer 外框 |
| `--radius-pill` | `9999px` | 仅 badge / 少数 chip；**主按钮不要做成全胶囊**（Claude 营销主 CTA 多为 ~8px） |

### 6.2 边框与阴影

- 默认分层：`1px solid var(--line)` 或 `rgba(18,18,18,0.06~0.12)`  
- 优先 **ring 阴影**：`box-shadow: 0 0 0 1px rgba(18,18,18,0.06)`  
- 卡片若需抬起：`0 2px 12px rgba(18,18,18,0.04~0.06)`，阴影色偏暖黑，**不要** `rgba(123,92,255,...)`  
- 标准白卡片在羊皮纸上可以**无阴影、仅发丝边**

### 6.3 选中 / Hover

- 导航选中：白或 `--surface-sand` 浅底 + 近黑字；不要强发光。  
- 链接 hover：近黑 → 陶土橙（可选）。  
- Focus：近黑 2px outline 或 `border-strong`，避免紫色 focus ring。

---

## 7. 控件角色（映射到现有组件，不改结构）

| 角色 | Claude 规则 | CodeAgent 映射建议 |
|------|-------------|-------------------|
| Primary CTA | 近黑底 + 奶油字，radius 8 | 发送、确认、主操作按钮 |
| Secondary | 透明/白底 + 深描边 | Open folder、次要工具 |
| Accent / Clay | 陶土橙底白字，**极少** | 可留给唯一品牌行动或 logo |
| Nav item | 无边无影；选中浅底 | `.nav-item` |
| Search / Input | 白底、发丝边、小圆角 | `.search`、composer |
| Card | 白底 + `#f0eee6` 边，大圆角 | suggestion cards、面板 |
| Badge | 陶土浅底 `#f3e7df` + 深陶土字 | cost chip、状态 pill |
| Avatar | 近黑或暖沙，**不要渐变** | `.avatar` |

营销页右侧「工作区」常见：**浅灰网格底 + 白圆角内容卡**——若现有右侧面板要贴近氛围，可用极淡网格或 `--surface-sand`，但**不新增布局区块**。

---

## 8. Claude Code Web 产品形态备忘（仅理解，不改布局）

公开文档描述 [claude.ai/code](https://claude.ai/code) 大致为：

- 左侧：会话列表（活跃 / 归档）  
- 中央：对话时间线  
- 底部：composer（仓库选择、权限模式、发送）  
- 可出现进度、diff、PR 摘要等

CodeAgent 已有「侧栏 + 主工作区 + composer」结构，**视觉迁移时应对齐气质，而不是重排 DOM**。

终端版 Claude Code 另有 dark/light theme token（`~/.claude/themes`），**本改版不以终端暗色主题为准**，而以网页暖纸编辑风为准。

---

## 9. 建议的 CSS 变量草案（落地时可直接替换 `:root`）

```css
:root {
  --bg: #f5f4ed;
  --panel: #ffffff;
  --text: #141413;
  --muted: #5e5d59;
  --line: #f0eee6;
  --accent: #c96442;
  --accent-soft: #f3e7df;
  --cta: #121212;
  --cta-text: #f8f8f6;
  --surface-sand: #e8e6dc;
  --shadow: 0 0 0 1px rgba(18, 18, 18, 0.06);
  --radius: 12px;
  --font: "IBM Plex Sans", "Noto Sans SC", system-ui, "Segoe UI", sans-serif;
  --font-display: "Source Serif 4", "Noto Serif SC", Georgia, serif;
  --font-mono: "IBM Plex Mono", ui-monospace, Consolas, monospace;
  /* 废弃：--grad 紫粉渐变 */
}
```

Google Fonts 引入示例：

```html
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&family=Noto+Sans+SC:wght@400;500;600&family=Noto+Serif+SC:wght@400;500&family=Source+Serif+4:opsz,wght@8..60,400;8..60,500&display=swap" rel="stylesheet" />
```

---

## 10. Do / Don't

### Do

- 暖羊皮纸底 + 近黑字 + 白卡片发丝边  
- 标题衬线、控件无衬线  
- 主按钮近黑；陶土橙克制使用  
- 细线单色图标  
- 字重偏轻、留白感靠颜色与字号，不靠重阴影  

### Don't

- 紫粉 / 霓虹渐变作为品牌或主 CTA  
- 全站只用一种粗无衬线加粗标题  
- 把陶土橙刷到进度条、每条导航、每个 chip  
- 深色霓虹「赛博」风格冒充 Claude Code Web  
- 为「更像」而增删功能模块或改 grid 列结构  

---

## 11. 参考来源

1. 用户提供的 Claude Code / Claude 产品页截图（Think fast, build faster）  
2. [Claude Code 产品页](https://claude.com/product/claude-code)  
3. [Claude Code on the web 文档](https://code.claude.com/docs/en/claude-code-on-the-web)  
4. [oh-my-design：Claude DESIGN.md](https://oh-my-design.kr/design-systems/claude)（2026-07-13 公开页实测 token）  
5. [webdesignhot：Claude.ai DESIGN.md](https://www.webdesignhot.com/design.md/claude-ai/)  
6. [Refero：Claude / Anthropic design system](https://styles.refero.design/style/47cb86b6-cb2d-41c8-94ba-8607cd7c41cd)  
7. [designmd：Claude Warm Parchment](https://designmd.app/library/claude-warm-parchment)  

说明：Anthropic 未发布可商用的完整官方 Design Tokens 包；上表为公开页面观测与第三方整理的**风格仿写规范**，用于 CodeAgent 视觉对齐，而非品牌资产授权。

---

## 12. 后续落地清单（待你确认后再改代码）

1. 替换 `styles.css` 的 `:root` 色板与字体变量；删除 `--grad` 用法。  
2. `index.html` 换成开源字体 link；品牌 SVG 去掉紫粉渐变，改为近黑或陶土单色。  
3. 标题类（如 `.hello`）改用 `--font-display`。  
4. 按钮 / chip / avatar / focus / shadow 全面去紫，改近黑 / 发丝边 / 暖阴影。  
5. **不修改** `app.js` 行为与 HTML 区块结构（除非仅为 class/样式钩子所必需且行为不变）。

---

*本文档只定义风格；实施前请确认：是否按第 9 节 token 直接改 web 静态资源。*
