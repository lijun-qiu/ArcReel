/**
 * 角色资产图 prompt 预览：与 lib/prompt_builders.py 的
 * build_character_prompt / build_character_look_prompt 对齐。
 * 结构同 d66785d1：画风原样前置 + 描述 + 布局 + 防崩；
 * 仅额外做描述清理（剥掉动作/手持等）。
 * 仅供 UI 展示；真正入队仍由服务端同一真相源拼装。
 */

const CHARACTER_LAYOUT =
  "横版 16:9 四格布局，纯白 (#FFFFFF) 背景：左侧约 40% 宽为胸像特写（清晰展示面部、发型、配饰、上装），" +
  "右侧三个等宽面板分别为正面 / 四分之三侧面 / 背面的 A-Pose 全身视图。";

const CHARACTER_GUARD =
  "四个面板中角色面部、发型、服装、配饰完全一致；五官对称、手指完整为五指、肢体比例协调。";

const NEGATIVE_TAIL_CHARACTER = "画面避免：水印、多余文字、Logo。";

const CHARACTER_NON_APPEARANCE_CLAUSE =
  /[^，。；、!\n]*?(?:手持|拿着|握着|捧着|挟着|夹着|拎着|扛着|背着|推着|拖着|嗑瓜子|二郎腿|盘腿|蹲着|坐着|坐在|躺着|跪着|走进|走入|跑向|冲进|踏入|站在|倚在|靠在|望向|看着|盯着|麻将馆|酒吧|酒馆|赌场|战场|掌心发光|觉醒|发动|正在|一边|同时)[^，。；、!\n]*/g;

const CHARACTER_PLOT_SENTENCE =
  /[^。；!\n]*?(?:本作|男主|女主|重生者|重生|前世|含恨|一朝回到|回到.{0,12}前|杀害|仇敌|决心|布局|囤物资|握战力|杀仇|妹妹|兄弟|至亲|守护|仇杀|最想|彩票|红雾降临|未来记忆|全部未来|打电话|约她|叮嘱|请你喝酒)[^。；!\n]*[。；!]?/g;

function stylePrefix(style = "", styleDescription = ""): string {
  const parts: string[] = [];
  const styleText = style.trim();
  const descriptionText = styleDescription.trim();
  if (styleText) parts.push(`风格：${styleText}`);
  if (descriptionText) parts.push(`描述：${descriptionText}`);
  return parts.length > 0 ? `${parts.join("\n")}\n\n` : "";
}

function sanitizeCharacterDescription(description: string): string {
  const text = description.trim();
  if (!text) return text;
  let cleaned = text
    .replace(CHARACTER_PLOT_SENTENCE, "")
    .replace(CHARACTER_NON_APPEARANCE_CLAUSE, "")
    .replace(/[，、]{2,}/g, "，")
    .replace(/[。；]{2,}/g, "。")
    .replace(/\s{2,}/g, " ")
    .replace(/^[，、。；\s]+|[，、。；\s]+$/g, "");
  if (!cleaned) return "需补充外貌与服装细节。";
  if (!/[。；!！？?]$/.test(cleaned)) cleaned += "。";
  return cleaned;
}

export function previewCharacterPrompt(
  name: string,
  description: string,
  style = "",
  styleDescription = "",
): string {
  const appearance = sanitizeCharacterDescription(description);
  return (
    `${stylePrefix(style, styleDescription)}` +
    `角色「${name}」的设计参考图。\n\n` +
    `${appearance}\n\n` +
    `${CHARACTER_LAYOUT}\n\n` +
    `${CHARACTER_GUARD}\n\n` +
    `${NEGATIVE_TAIL_CHARACTER}`
  );
}

export function previewCharacterLookPrompt(
  name: string,
  lookId: string,
  description: string,
  {
    baseDescription = "",
    style = "",
    styleDescription = "",
  }: {
    baseDescription?: string;
    style?: string;
    styleDescription?: string;
  } = {},
): string {
  const appearance = sanitizeCharacterDescription(description);
  const baseAppearance = sanitizeCharacterDescription(baseDescription);
  const baseBlock = baseAppearance ? `基准外形：${baseAppearance}\n\n` : "";
  return (
    `${stylePrefix(style, styleDescription)}` +
    `角色「${name}」的衣橱造型「${lookId}」设计参考图。` +
    `须与参考图中的同一人物保持脸型、五官、体型一致，仅按下列造型差异改服装或大战损外观。\n\n` +
    `${baseBlock}` +
    `本造型差异：${appearance}\n\n` +
    `${CHARACTER_LAYOUT}\n\n` +
    `${CHARACTER_GUARD}\n\n` +
    `${NEGATIVE_TAIL_CHARACTER}`
  );
}
