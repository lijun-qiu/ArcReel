---
name: generate-assets
description: >-
  统一资产生成 skill：接受 `--type=character|scene|prop`，或不传自动扫所有 pending（缺 sheet）资源并按类型分发。当用户说“生成角色图”/“生成场景图”/“生成道具图”、想为新资产创建参考图、或有资产缺少 *_sheet 时使用。
---

# 生成资产图

为项目的角色、场景、道具创建资产图，保证整个视频中视觉元素的一致性。
图像供应商由项目设置选择（不锁定具体 backend）。

> Prompt 编写原则详见 `.claude/references/generation-modes.md` 的"Prompt 语言"章节。

## 共同约定

- 所有资产 `description` 用**叙事式段落**，而不是关键词列表。
- 用户只需在 project.json 中维护 `description`；最终交给图像 backend 的完整 prompt
  （含布局 / 防崩短语 / 反向提示词）由 `lib/prompt_builders.py` 在 server 端拼好，
  WebUI 与 Skill 走同一份真相源。
- Pending 判定：Artifact Manifest 中该资产图状态为 `missing`；`stale` 产物复用，不计入待生成。

---

## 角色（character）

### description 编写指南

用连贯段落描述外貌、服装、神情，包含年龄、体态、面部特征、服饰细节。

**基准造型只写站着就能看见的外形**：年龄体态、五官、发型、神情；主装写已有的颜色与款式（没有就不编造）。瘦小、黄牙、旧疤、油滑神情等身份特征要保留。衣着按角色自己的服装来写；出图时的衣着平整由生成 prompt 负责，不要把配角改成偶像光洁脸。
不写姿势动作（二郎腿、嗑瓜子）与出没场所；能力发动光效写在镜头正文，不进 description，也不建衣橱。
同一段里不并列两套日常装。

**示例**：

> "二十出头的女子，身材纤细，鹅蛋脸上有一双清澈的杏眼，柳叶眉微蹙时带着几分忧郁。身着淡青色绣花罗裙，腰间系着同色丝带，显得端庄而不失灵动。"

### 输出布局

横版 16:9 四格设计稿，纯白背景：左侧约 40% 宽度的胸像特写，右侧三个 A-Pose 全身视图（正面 / 四分之三侧面 / 背面）。
所有面板中角色面部、发型、服装颜色与款式、配饰需保持完全一致。

> 用户填写 description 时只需关心外貌 / 服装等内容；布局由 builder 注入。

---

## 场景（scene）

### description 编写指南

用连贯段落描述形态、光线、氛围，突出能跨场景识别的独特特征。

**必须是无人环境**：不写人物、人影、保安、路人、厨师、丧尸；可保留空间陈设与氛围（废墟可破败）。description 里出现的人物会被画进 sheet。

**示例**：

> "村口的百年老槐树，树干粗壮需三人合抱，树皮龟裂沧桑。主干上有一道明显的雷击焦痕，从顶部蜿蜒而下。树冠茂密，夏日里洒下斑驳的树影。"

### 输出布局

单帧连续空间的环境空镜（整幅只有一层环境）。审核时若画面里多出第二层嵌套画面，删 sheet 后整图重画，不要用 edit 修局部——局部编辑会把那层结构保留下来。生成 prompt 里不要点名角落、窗框或职业名，点名会把它们画出来。

---

## 道具（prop）

### description 编写指南

用连贯段落描述形态、质感、细节，突出能跨场景识别的独特特征。

**只画物件本体**：不写手持者、使用动作、周围人物。
**只写跨镜头稳定的外形**；不要写入某一瞬间的屏幕内容、推送文案、备忘录正文、APP 界面状态或日期倒计时——瞬时 UI 写在下游镜头正文里。手机 / 电脑等带屏设备描述机型、边框、配色即可（生成图会按熄屏 / 中性模糊屏处理）。

**示例**：

> "一块翠绿色的祖传玉佩，约拇指大小，玉质温润透亮。表面雕刻着精致的莲花纹样，花瓣层层舒展。玉佩上系着一根红色丝绳，打着传统的中国结。"

> "黑色全面屏智能手机，窄边框，机身哑光，无剧情专属屏幕文案。"

### 输出布局

三视图水平排列于纯净浅灰背景：正面全视图、45° 侧视图、关键细节特写。禁止人物肖像夹道具。

---

## 工具调用

入队走 MCP 工具：

| 操作 | 工具 |
|------|------|
| 列出所有/某类 pending | `mcp__arcreel__list_pending_assets({"type": "character"})`（type 可省略） |
| 生成所有 pending（三类各一轮） | `mcp__arcreel__generate_assets({})` |
| 生成某类全部 pending | `mcp__arcreel__generate_assets({"type": "character"})` |
| 生成指定多个 | `mcp__arcreel__generate_assets({"type": "prop", "names": ["玉佩", "密信"]})` |
| 生成单个 | `mcp__arcreel__generate_assets({"type": "scene", "names": ["村口老槐树"]})` |

结果按 `requested / succeeded / failed / blocked / skipped` 逐 ID 返回，ID 形如 `character/张三` 或 `character/张三__战损`；
已失效但可复用的旧图进入 `skipped`，不会自动重生；
按每一项自带的 `problem.code` 与 `problem.action` 决定下一步，不要解析文本。
结构详见 `.claude/references/generation-results.md`。

## 工作流程

1. **加载项目元数据** — 从 Artifact Manifest 找出资产图状态为 `missing` 的资产
2. **入队生成任务** — description 直接作为 prompt 提交；server 端 `lib.prompt_builders` 注入布局 / 防崩 / 反向
3. **审核检查点** — 展示每张资产图，用户可批准、要求重新生成，或要求编辑
4. **更新 project.json** — 更新 `character_sheet` / `scene_sheet` / `prop_sheet` 路径

## 审核检查点：编辑 vs 重新生成

用户对资产图提意见时先判断诉求类型，选错路径会推翻已满意的部分或丢掉预期外的改动：

- **只想改局部**（换发色、去掉杂物、调整光线氛围等），且构图和整体设计满意 → 用
  `mcp__arcreel__edit_images({"resource_type": "character", "edits": [{"id": "张三", "instruction": "把头发改成红色"}]})`
  保底图微调，一次可对同类型多个资产批量下发
- **场景图右下角仍有嵌套小窗 / 画中画**：不要用 edit 去「修角标」（i2i 常把小窗结构保留）；删 sheet 后 `generate_assets` 整图重画
- **想推翻构图/整体设计重来**，或本来就要改 description（进而改变后续按 description
  重新生成的结果）→ 用 `generate_assets` 按更新后的 description 重新生成整图
- 编辑不会更新 `description` / prompt——编辑后再触发 `generate_assets` 仍按原 description
  重画，编辑效果只能从版本历史找回

## 质量检查

- **角色 base**：一套主装四面板同色同款；站立双手自然下垂；五官与身份特征跟 description 一致；整图媒介与光色跟项目画风一致
- **角色 look**：只表达相对 base 的跨镜换装或大战损；脸型体型与 base 一致
- **场景**：无人（含人影/剪影/面部）；单帧连续空间、无嵌套窗框/软件窗；整体构图与标志性特征突出、光线氛围合适
- **道具**：仅物件本体三视图，无人物/手部；细节符合描述、特殊纹理清晰可见
- 不达标则删除该 sheet，把 description 改成日常定装后再 `generate_assets`；未通过的图不进入下游
