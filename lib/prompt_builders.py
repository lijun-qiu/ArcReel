"""图像 / 视频 / 资产 prompt 的统一真相源。

WebUI（server/services/generation_tasks.py）和 Skill（agent_runtime_profile/.claude/skills/generate-assets）
都从这里取最终 prompt 文本，确保入口一致、不漂移。

设计要点：
- 无 backend 锁定：纯文本拼接，由调用方决定走哪个 image/video provider。
- 反向提示词统一以「画面避免：xxx」追加到 prompt 末尾，不再使用各 backend 的 negative_prompt 参数通道
  （image backends 大多 silent 丢弃，参数化反而增加分叉）。
- 防崩与反向短语精简：只保关键项，避免 CFG 权重稀释。
- 反向提示词按图种各自定义，内容相同也不合并：合并后若要单独调整其中一类仍需先拆分常量，
  且合并的常量无法表达各图种之间是必须一致还是恰好相同。
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from lib.prompt_utils import image_prompt_to_yaml, project_storyboard_image_prompt

# ---------------------------------------------------------------------------
# 内部常量：防崩 / 反向 / 布局 / 风格前缀
# ---------------------------------------------------------------------------

# 角色图采用 issue #353 的四视图 16:9 布局。
_CHARACTER_LAYOUT = (
    "横版 16:9 四格布局，纯白 (#FFFFFF) 背景：左侧约 40% 宽为胸像特写（清晰展示面部、发型、配饰、上装），"
    "右侧三个等宽面板分别为正面 / 四分之三侧面 / 背面的 A-Pose 全身视图。"
)
# 场景常见失败是「概念设定板」先验（嵌套小窗 / 软件窗框）。
# 只用正向构图句压住；点名「右下角 / 画中画 / UI 窗 / 软件窗框」反而会诱发对应画面。
_SCENE_LAYOUT = (
    "构图硬性要求：一张完整的环境空镜照片式构图，"
    "画面从左到右、从上到下都是同一连续空间里的建筑与陈设。"
)
_PROP_LAYOUT = "三视图水平排列于纯净浅灰背景：左侧正面全视图、中间 45° 侧视图体现立体感、右侧关键细节特写。"
_PRODUCT_LAYOUT = (
    "标准多角度产品参考图，纯净浅灰背景、均匀棚拍布光：正面、45° 侧面、背面三视图水平排列，"
    "下方一排关键细节特写（logo、文字、材质、接缝）。"
)

# 正向防崩（按资产类型差异化）。
# 角色 guard / negative 口径对齐 d66785d1：画风原样前置，不再追加定装硬改写。
_CHARACTER_GUARD = "四个面板中角色面部、发型、服装、配饰完全一致；五官对称、手指完整为五指、肢体比例协调。"
# 场景 description 由剧本提取，常包含人物动作与剧情事件；项目 style 也可能偏角色立绘，
# 仅靠末尾反向提示词不足，须在正向句中再声明「环境空镜、非角色图」。道具同理：style 渗漏
# 时易画出人物肖像夹道具，故正向声明「孤立物件、非角色图」。带屏设备的瞬时 UI 守卫不变。
# 守卫不枚举职业/物种名（厨师、保安、丧尸等）：点名否定会诱发对应形象。
# 不写「人 / 面孔 / 身体 / 窗框」：点名否定会把对应物画出来。空场只用正向句。
_SCENE_GUARD = (
    "这是单一镜头的环境空镜参考图，只画建筑、地面、天空与固定陈设。"
    "场面为空场。空间透视正常，陈设固定，光影统一。"
)
_PROP_GUARD = (
    "这是孤立道具三视图参考图，不是角色设计图。"
    "浅灰纯净背景上只画道具本体；禁止人物、肖像、手部、肢体残影或角色半身。"
    "外观结构完整，焦点清晰，禁止与其他物件融合。"
    "若为手机 / 电脑 / 平板等带屏设备：屏幕须熄屏或显示无法辨认文字的中性模糊界面，"
    "禁止绘制剧情专属文字、推送通知、备忘录正文、日期倒计时或可辨认 UI 文案"
    "（瞬时屏幕内容由下游镜头正文描述，不进共享道具参考图）。"
)
# 产品保真核心句：sheet 生成守卫与镜头注入指令共用，调优措辞只改这一处。
_PRODUCT_FIDELITY_CORE = "logo、文字、配色、材质、比例与结构不得改变或臆造"
# product sheet 由实拍原图整理而来，原图全量作为 i2i 参考注入（generation_tasks.py 的
# _DESIGN_REFERENCE_COLLECTORS），手持与模特展示是电商原图的常见形态。参考图里的真人是强
# 正向视觉条件，末尾的反向提示词压不住，因此在守卫句中正面声明只呈现产品本体。这句只作用于
# sheet 生成；产品出现在镜头里时本就可以被人拿着，不能走 _PRODUCT_FIDELITY_CORE 共用。
_PRODUCT_GUARD = (
    f"产品外观必须忠实于参考图中的真实产品：{_PRODUCT_FIDELITY_CORE}；各视图为同一件产品。"
    "参考图中的手部、模特及其他出镜人物一律不保留，画面只呈现产品本体；"
    "包装上印刷的人像图案属于产品外观，须原样保留。"
)

# 反向提示词：只列实体排除项，不写质量词（质量词对现代生成模型近于噪声，且稀释 CFG 权重）。
# 人物排除项仅用于展示环境或物件的图种；角色图与分镜图的画面主体本身就是人物，加入该排除项
# 会损害生成结果。写「出镜人物」而非「人物」，是为了把排除范围限定在进入画面的人：画像、造像、
# 人偶这类道具，以及包装上印有人物图案的产品，其人像属于物件本体，与 _PROP_GUARD /
# _PRODUCT_GUARD 要求的外观忠实一致，排除项不应波及。
_NEGATIVE_TAIL_CHARACTER = "画面避免：水印、多余文字、Logo。"
_NEGATIVE_TAIL_SCENE = (
    "画面避免：出镜人物、面孔、身体局部、水印、多余文字、Logo。"
)
_NEGATIVE_TAIL_PROP = "画面避免：出镜人物、手部、肖像、角色半身、水印、多余文字、Logo。"
_NEGATIVE_TAIL_PRODUCT = "画面避免：出镜人物、水印、多余文字、Logo。"
_NEGATIVE_TAIL_STORYBOARD = (
    "画面避免：水印、多余文字、Logo、对镜头摆拍、证件照式正脸对视镜头、偶像海报站位。"
)
_NEGATIVE_TAIL_VIDEO = "禁止出现：BGM、文字字幕、水印。"


def _style_prefix(style: str = "", style_description: str = "") -> str:
    """组合视觉风格前缀。两者都为空时返回空串。"""
    parts = []
    if style:
        parts.append(f"风格：{style}")
    if style_description:
        parts.append(f"描述：{style_description}")
    if not parts:
        return ""
    return "\n".join(parts) + "\n\n"


def _non_character_style_prefix(style: str = "", style_description: str = "") -> str:
    """场景 / 道具用风格前缀：保留画风，去掉角色外貌关键词，避免 style 渗漏出人物立绘。

    项目 style 常写「角色脸型 / V脸 / 发型」等立绘口径；原样注入场景与道具 prompt 时，
    模型会把 sheet 画成角色半身夹环境/物件。此处只保留媒介与光色类表述，并显式声明
    本图不绘制人物。场景/道具会剥掉画风里的立绘默认词（气质清爽时尚、中分或侧分、偶像等），
    避免残留脸型词渗出人物。角色图不走这里，画风原文原样保留。
    """
    text = " ".join(part for part in (style, style_description) if part and part.strip()).strip()
    if not text:
        # 无项目画风时不臆造媒介；只保留「勿画人」声明，避免空 style 被写成 2D 插画。
        return "本图只画环境或物件，不画人物。\n\n"
    # 先清整段立绘默认值，再清残余短触发词。
    scrubbed = re.sub(
        r"(?:角色脸型|脸型)[^，。；、,\n]{0,80}"
        r"|花美男偶像感[^，。；、,\n]{0,40}"
        r"|花美男"
        r"|偶像感"
        r"|偶像"
        r"|气质清爽时尚"
        r"|清爽时尚"
        r"|大眼精致五官"
        r"|大眼精致"
        r"|窄长尖下巴V脸"
        r"|尖下巴V脸"
        r"|V脸"
        r"|中分或侧分刘海贴脸"
        r"|中分或侧分"
        r"|刘海贴脸"
        r"|发型蓬松分层[^，。；、,\n]{0,40}"
        r"|服饰偏青年休闲西装外套与浅色内搭[^，。；、,\n]{0,40}"
        r"|青年休闲西装外套与浅色内搭"
        r"|西装外套与浅色内搭"
        r"|青年休闲西装外套"
        r"|西装外套"
        r"|浅色内搭"
        r"|写实路人脸"
        r"|路人脸"
        r"|平头短发"
        r"|拒绝当代主流写实路人脸与平头短发"
        r"|拒绝当代主流与"
        r"|(?:角色|人物|五官|发型|刘海|瞳孔|身材|服装|服饰|外套|内搭|"
        r"尖下巴|大眼|窄长)[^，。；、,\.;]{0,24}",
        "",
        text,
    )
    scrubbed = re.sub(r"[，。；、,\s]{2,}", "，", scrubbed)
    scrubbed = re.sub(r"(?:拒绝[^，。]{0,8}与)|(?:与。)", "", scrubbed)
    scrubbed = re.sub(r"[，。；、,\s]{2,}", "，", scrubbed).strip("，。；、, \t\n")
    if not scrubbed:
        # style 几乎全是立绘词时，只留防人物声明，不硬塞默认画风。
        return "本图只画环境或物件，不画人物、肖像或半身立绘。\n\n"
    return f"画风：{scrubbed}。本图只采用上述媒介与光色；只画环境或物件，不画人物、肖像或半身立绘。\n\n"


# 场景 description 里残留的人物 / 情节子句会压过守卫句；生成前剥掉，保留环境陈设。
_SCENE_PEOPLE_CLAUSE = re.compile(
    r"[^。；!\n]*?(?:打手|保安|厨师|路人|群演|丧尸|摊贩|围观|巡逻|阵列|"
    r"人影|人群|行人|男子|女子|男孩|女孩|"
    r"在此设|在此布置|有人|等人|角色)[^。；!\n]*[。；!]?"
)


def _sanitize_scene_description(description: str) -> str:
    """去掉会把人物画进环境 sheet 的子句；保留建筑与固定陈设，并钉死空场。"""
    text = (description or "").strip()
    if not text:
        return "场面为空场，只保留建筑与固定陈设。"
    cleaned = _SCENE_PEOPLE_CLAUSE.sub("", text)
    cleaned = re.sub(r"[，、]{2,}", "，", cleaned)
    cleaned = re.sub(r"[。；]{2,}", "。", cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip("，、。； \t\n")
    if not cleaned:
        cleaned = "只保留建筑与固定陈设"
    # 「无人 / 空无一人」里的「人」会诱发人物，统一改成不含该字的空场。
    cleaned = cleaned.replace("空无一人", "空场").replace("无人", "空场")
    if "空场" not in cleaned:
        cleaned = f"{cleaned.rstrip('。；')}。场面为空场，只保留建筑与固定陈设"
    return cleaned.rstrip("。；") + "。"


# 角色定装 description 只允许站着就能看见的外形。姿势动作、手持、出没场所、能力瞬间、
# 剧情关系简介属于镜头/人设正文；注入 sheet 前按分句剥掉，避免定装图被剧情冲淡画风。
# 分句而不是整句：服装与剧情写在同一句时，只丢掉含剧情词的子句，留下衣服款式与颜色。
# 与 analyze-assets 视觉口径一致；对齐早期能按画风出图时的外形-only 描述。
_CHARACTER_CLAUSE_SPLIT = re.compile(r"[，。；、：!\n！？?]+")
_CHARACTER_NON_APPEARANCE_CLAUSE = re.compile(
    r"手持|拿着|握着|捧着|挟着|夹着|拎着|扛着|背着|推着|拖着|"
    r"嗑瓜子|二郎腿|盘腿|蹲着|坐着|坐在|躺着|跪着|"
    r"走进|走入|跑向|冲进|踏入|站在|倚在|靠在|望向|看着|盯着|"
    r"麻将馆|酒吧|酒馆|赌场|战场|"
    r"掌心发光|觉醒|发动|"
    r"正在|一边|同时"
)
_CHARACTER_PLOT_CLAUSE = re.compile(
    r"本作|男主|女主|重生者|重生|前世|含恨|一朝回到|回到.{0,12}前|"
    r"杀害|仇敌|决心|布局|囤物资|握战力|杀仇|"
    r"妹妹|兄弟|至亲|守护|仇杀|最想|"
    r"彩票|红雾降临|未来记忆|全部未来|"
    r"打电话|约她|叮嘱|请你喝酒"
)


def _sanitize_character_description(description: str) -> str:
    """去掉姿势动作、手持、场所、能力瞬间与剧情简介；保留性别年龄五官发型衣着等外形。"""
    text = (description or "").strip()
    if not text:
        return text
    kept: list[str] = []
    for clause in _CHARACTER_CLAUSE_SPLIT.split(text):
        clause = clause.strip()
        if not clause:
            continue
        if _CHARACTER_PLOT_CLAUSE.search(clause) or _CHARACTER_NON_APPEARANCE_CLAUSE.search(clause):
            continue
        kept.append(clause)
    cleaned = "，".join(kept).strip("，、。； \t\n")
    if not cleaned:
        return "需补充外貌与服装细节。"
    return cleaned + "。"


# ---------------------------------------------------------------------------
# 资产 prompt（character / scene / prop）
# ---------------------------------------------------------------------------


def build_character_prompt(name: str, description: str, style: str = "", style_description: str = "") -> str:
    """角色设计图 prompt（issue #353 四视图 16:9）。

    结构对齐 d66785d1：``风格：`` 原样前置 + 描述 + 布局 + 防崩。
    仅保留描述清理：剥掉动作 / 手持 / 场所等非外形子句。
    """
    style_block = _style_prefix(style, style_description)
    appearance = _sanitize_character_description(description)
    return (
        f"{style_block}"
        f"角色「{name}」的设计参考图。\n\n"
        f"{appearance}\n\n"
        f"{_CHARACTER_LAYOUT}\n\n"
        f"{_CHARACTER_GUARD}\n\n"
        f"{_NEGATIVE_TAIL_CHARACTER}"
    )


def build_character_look_prompt(
    name: str,
    look_id: str,
    description: str,
    *,
    base_description: str = "",
    style: str = "",
    style_description: str = "",
) -> str:
    """非基准造型：与基准角色图同一套拼接，仅标题与差异说明不同。

    基准 sheet 仅作脸型 / 五官 / 体型参考；服装与配饰不得锁死在基准图上——
    左侧胸像与右侧全身三视图都必须换成「本造型差异」的衣着（i2i 易粘住胸像区）。
    """
    style_block = _style_prefix(style, style_description)
    appearance = _sanitize_character_description(description)
    base_appearance = _sanitize_character_description(base_description)
    base_block = (
        f"基准身份（只保留脸型、五官、体型、发型；不要沿用其服装或配饰）：{base_appearance}\n\n"
        if base_appearance
        else ""
    )
    return (
        f"{style_block}"
        f"角色「{name}」的衣橱造型「{look_id}」设计参考图。"
        f"参考图只用于同一人物的脸型、五官与体型一致；"
        f"服装、上装、配饰不得锁死在参考图上。"
        f"左侧胸像特写与右侧正面 / 侧面 / 背面全身四格，"
        f"必须全部换成下列本造型的服装或大战损外观，禁止半套换装。\n\n"
        f"{base_block}"
        f"本造型服装与外观：{appearance}\n\n"
        f"{_CHARACTER_LAYOUT}\n\n"
        f"{_CHARACTER_GUARD}\n\n"
        f"{_NEGATIVE_TAIL_CHARACTER}"
    )


def build_scene_prompt(name: str, description: str, style: str = "", style_description: str = "") -> str:
    """场景空镜 prompt（单帧连续空间，无嵌套窗框）。

    构图约束放在 description 之前：模型对「设定板 + 角标」先验很强，后置禁止句经常无效。
    description 在注入前剥掉人物/情节子句，避免提取残留压过守卫。
    """
    style_block = _non_character_style_prefix(style, style_description)
    scene_desc = _sanitize_scene_description(description)
    return (
        f"{style_block}"
        f"场景「{name}」的环境空镜。\n\n"
        f"{_SCENE_LAYOUT}\n\n"
        f"{scene_desc}\n\n"
        f"{_SCENE_GUARD}\n\n"
        f"{_NEGATIVE_TAIL_SCENE}"
    )


def build_prop_prompt(name: str, description: str, style: str = "", style_description: str = "") -> str:
    """道具设计图 prompt（三视图）。"""
    style_block = _non_character_style_prefix(style, style_description)
    return (
        f"{style_block}"
        f"道具「{name}」的多视角展示。\n\n"
        f"{description}\n\n"
        f"{_PROP_LAYOUT}\n\n"
        f"{_PROP_GUARD}\n\n"
        f"{_NEGATIVE_TAIL_PROP}"
    )


def build_product_prompt(name: str, description: str, style: str = "", style_description: str = "") -> str:
    """产品标准参考图（product sheet）prompt（多角度 + 保真守卫）。

    产品 sheet 的使命是把用户随手拍的原图整理成标准多角度设计图，产品形象必须
    忠实于真品（原图作为参考注入），不沿用项目画风前缀——画风统一由项目级 style
    机制在分镜阶段承载，产品参考图保持写实中性。
    """
    del style, style_description  # 与其它 design prompt builder 签名对齐；产品 sheet 不注入画风
    return (
        f"产品「{name}」的标准参考图。\n\n"
        f"{description}\n\n"
        f"{_PRODUCT_LAYOUT}\n\n"
        f"{_PRODUCT_GUARD}\n\n"
        f"{_NEGATIVE_TAIL_PRODUCT}"
    )


# ---------------------------------------------------------------------------
# 分镜 / 视频 prompt 末尾增强
# ---------------------------------------------------------------------------


def build_storyboard_prompt(
    image_prompt: object,
    style: str = "",
    style_description: str = "",
) -> str:
    """Render canonical storyboard semantics into the exact provider prompt."""

    if not isinstance(style_description, str):
        raise TypeError("style_description must be a string")
    projected, normalized_style = project_storyboard_image_prompt(image_prompt, style)

    style_parts: list[str] = []
    if normalized_style and isinstance(projected, str):
        style_parts.append(f"Style: {normalized_style}")
    normalized_description = style_description.strip()
    if normalized_description:
        style_parts.append(f"Visual style: {normalized_description}")
    style_prefix = "\n".join(style_parts) + "\n\n" if style_parts else ""
    rendered = image_prompt_to_yaml(projected, normalized_style) if isinstance(projected, dict) else projected
    if style_prefix and not rendered.startswith(style_prefix):
        rendered = f"{style_prefix}{rendered}"
    return append_image_negative_tail(rendered)


def append_product_fidelity_tail(prompt: str, product_names: Sequence[str] | None) -> str:
    """给产品镜头的生成 prompt 追加高保真还原指令。

    仅在产品参考图实际注入请求时调用（分镜图与视频两层共用同一份指令文本）——
    指令指向"产品参考图"，参考缺席时追加只会误导模型。``product_names`` 为空
    （含 None 脏数据）返回原 prompt；重复调用幂等。误传单个字符串按单产品名处理
    （str 本身满足 Sequence[str]，按字符迭代会拼出逐字括注的畸形指令）。

    显式声明优先于此前反向约束里的文字/Logo 禁止项（``_NEGATIVE_TAIL_*`` /
    ``_WATERMARK_PACK`` 等通用反向提示词）——那些约束防的是画面里凭空多出的水印/Logo，
    与「产品参考图本身自带的品牌标识须原样保留」并不矛盾，但两条指令的字面文本在同一
    prompt 里共存时对模型是冲突信号，需要显式排出优先级。
    """
    if not product_names:
        return prompt
    if isinstance(product_names, str):
        product_names = (product_names,)
    names = "".join(f"「{name}」" for name in product_names if name)
    if not names:
        return prompt
    tail = (
        f"产品高保真还原（最高优先级，优先于前述文字/Logo 禁止项）：画面中的产品{names}"
        f"必须与产品参考图完全一致——{_PRODUCT_FIDELITY_CORE}，不得重新设计或美化产品本身；"
        "前述文字/Logo 禁止项仅指画面中不得凭空新增文字或 Logo，产品参考图自带的文字与 Logo"
        "须原样保留；项目画风只作用于产品以外的画面元素。"
    )
    if not prompt or not prompt.strip():
        return tail
    if tail in prompt:
        return prompt
    return f"{prompt.rstrip()}\n\n{tail}"


def append_image_negative_tail(prompt: str) -> str:
    """给分镜图生成 prompt 追加统一的图像反向提示词。

    资产图在各 build_*_prompt 内已拼接各自的反向提示词；分镜图 prompt 由 LLM 产出、
    经归一化后交 image backend，在归一化出口过一遍此函数，保持各图像路径一致。
    """
    if not prompt or not prompt.strip():
        return _NEGATIVE_TAIL_STORYBOARD
    if _NEGATIVE_TAIL_STORYBOARD in prompt:
        return prompt
    return f"{prompt.rstrip()}\n\n{_NEGATIVE_TAIL_STORYBOARD}"


def append_video_negative_tail(prompt: str) -> str:
    """给视频生成 prompt 追加统一的反向提示词。

    调用方拿到分镜 video_prompt 文本后，在交给 video backend 之前过一遍此函数；
    避免在每个 caller 各自拼接、导致漂移。
    """
    if not prompt or not prompt.strip():
        return _NEGATIVE_TAIL_VIDEO
    if _NEGATIVE_TAIL_VIDEO in prompt:
        return prompt
    return f"{prompt.rstrip()}\n\n{_NEGATIVE_TAIL_VIDEO}"
