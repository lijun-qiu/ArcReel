import pytest

from lib.prompt_builders import (
    append_image_negative_tail,
    append_product_fidelity_tail,
    append_video_negative_tail,
    build_character_look_prompt,
    build_character_prompt,
    build_product_prompt,
    build_prop_prompt,
    build_scene_prompt,
)


class TestCharacterPrompt:
    @pytest.mark.unit
    def test_includes_supplied_character_details(self):
        prompt = build_character_prompt(
            "姜月茴",
            "黑发，冷静神态。",
            style="古风",
            style_description="Cinematic, low-key lighting",
        )
        assert "姜月茴" in prompt
        assert "黑发，冷静神态。" in prompt
        assert "古风" in prompt
        assert "Cinematic, low-key lighting" in prompt
        assert "风格：古风" in prompt
        assert "禁止漂成真人" not in prompt
        assert prompt.index("风格：古风") < prompt.index("黑发，冷静神态。")


class TestScenePromptAndPropPrompt:
    @pytest.mark.unit
    def test_prop_includes_supplied_details(self):
        prompt = build_prop_prompt("玉佩", "古朴温润")
        assert "玉佩" in prompt
        assert "古朴温润" in prompt

    @pytest.mark.unit
    def test_prop_guards_against_episode_specific_screen_ui(self):
        """共享道具 sheet 不得固化某一镜的屏幕文案，否则后续引用会串戏。"""
        prompt = build_prop_prompt(
            "手机",
            "智能手机，屏幕显示2024年7月15日，推送栏红字显示'距离红雾降临还有90天'",
        )
        assert "熄屏" in prompt or "中性" in prompt
        assert "剧情专属" in prompt or "备忘录正文" in prompt

    @pytest.mark.unit
    def test_prop_declares_isolated_object_not_character_sheet(self):
        prompt = build_prop_prompt("弩", "哑光黑狩猎弩")
        assert "不是角色设计图" in prompt
        assert "禁止人物" in prompt or "手部" in prompt

    @pytest.mark.unit
    def test_scene_includes_supplied_details(self):
        prompt = build_scene_prompt("祠堂", "昏暗古朴")
        assert "祠堂" in prompt
        assert "昏暗古朴" in prompt

    @pytest.mark.unit
    def test_scene_declares_empty_environment_not_character_sheet(self):
        prompt = build_scene_prompt("仓库", "混凝土墙面")
        assert "环境空镜" in prompt
        assert "场面为空场" in prompt
        assert "厨师" not in prompt
        assert "保安" not in prompt
        assert "窗框" not in prompt

    @pytest.mark.unit
    def test_scene_forbids_inset_picture_in_picture(self):
        prompt = build_scene_prompt("卧室", "简陋出租屋")
        # 构图约束须出现在 description 之前；且不点名右下角/画中画（点名会诱发）。
        assert prompt.index("构图硬性要求") < prompt.index("简陋出租屋")
        assert "连续空间" in prompt
        assert "右下角" not in prompt
        assert "画中画" not in prompt
        assert "picture-in-picture" not in prompt
        assert "窗框" not in prompt
        assert "软件窗" not in prompt
        assert "嵌入关键细节小图" not in prompt
        assert "视觉参考" not in prompt

    @pytest.mark.unit
    def test_scene_sanitizes_people_and_plot_from_description(self):
        prompt = build_scene_prompt(
            "城南废弃水泥厂",
            "厂区中央空地临时拉了几盏大功率灯泡。黑蛇在此设折叠椅与打手阵列。",
        )
        assert "打手" not in prompt
        assert "黑蛇在此设" not in prompt
        assert "灯泡" in prompt
        assert "场面为空场" in prompt or "只保留建筑" in prompt

    @pytest.mark.unit
    def test_scene_and_prop_strip_character_cues_from_style(self):
        style = "都市国漫二次元偶像插画，角色脸型偏窄长尖下巴V脸、大眼精致五官，2D数字绘画，气质清爽时尚，中分或侧分刘海贴脸"
        scene = build_scene_prompt("祠堂", "昏暗古朴", style=style)
        prop = build_prop_prompt("玉佩", "古朴温润", style=style)
        assert "V脸" not in scene and "大眼" not in scene
        assert "V脸" not in prop and "大眼" not in prop
        assert "气质清爽时尚" not in scene
        assert "中分或侧分" not in scene
        assert "偶像" not in scene
        assert "不画人物" in scene
        assert "不画人物" in prop
        assert "2D数字绘画" in scene or "画风" in scene

    @pytest.mark.unit
    def test_scene_and_prop_empty_style_does_not_invent_medium(self):
        scene = build_scene_prompt("祠堂", "昏暗古朴")
        prop = build_prop_prompt("玉佩", "古朴温润")
        assert "2D数字插画" not in scene
        assert "2D数字插画" not in prop
        assert "不画人物" in scene
        assert "不画人物" in prop


class TestCharacterBasePrompt:
    """角色 prompt 对齐 d66785d1 简单拼接，仅额外做描述清理。"""

    @pytest.mark.unit
    def test_character_prompt_matches_d66785d1_shape(self):
        prompt = build_character_prompt("林北", "青年男性，浅灰外套")
        assert "设计参考图" in prompt
        assert "基准造型" not in prompt
        assert "衣着崭新平整" not in prompt
        assert "以角色描述为准" not in prompt
        assert "四格布局" in prompt
        assert "五官对称" in prompt
        assert "站立 A-Pose" not in prompt  # A-Pose 只在 layout，不在 guard 硬改写句
        assert "A-Pose" in prompt
        assert "画面避免：水印、多余文字、Logo。" in prompt
        assert "面板间服装颜色不一致" not in prompt

    @pytest.mark.unit
    def test_character_prompt_keeps_style_text_and_identity_traits(self):
        style = "都市国漫二次元偶像插画，花美男偶像感，气质清爽时尚，青年休闲西装外套"
        prompt = build_character_prompt(
            "六子",
            "三十多岁瘦小男性，油滑神情，牙齿发黄。常穿廉价短袖与松垮长裤。",
            style=style,
        )
        assert "牙齿发黄" in prompt
        assert "瘦小" in prompt
        assert f"风格：{style}" in prompt
        assert "禁止漂成真人" not in prompt
        assert prompt.index("风格：") < prompt.index("牙齿发黄")
        assert prompt.index("风格：") < prompt.index("设计参考图")

    @pytest.mark.unit
    def test_character_prompt_keeps_project_style_verbatim(self):
        style = (
            "银翼杀手2049风格，极简野蛮主义赛博朋克，只用一种颜色来统治画面，"
            "粗野主义巨物建筑，气象级的环境粒子，留白"
        )
        prompt = build_character_prompt("林北", "湿发贴额，眼眶泛红。", style=style)
        assert f"风格：{style}" in prompt
        assert "禁止漂成真人仿真人" not in prompt
        assert "不得漂成写实摄影" not in prompt

    @pytest.mark.unit
    def test_character_prompt_keeps_appearance_only_description(self):
        prompt = build_character_prompt(
            "林北",
            "二十多岁青年男性，短黑发，黑色外套。手持手枪走进废墟，坐在破椅上嗑瓜子。",
            style="银翼杀手2049风格",
        )
        assert "二十多岁青年男性" in prompt
        assert "短黑发" in prompt
        assert "黑色外套" in prompt
        assert "手持手枪" not in prompt
        assert "走进废墟" not in prompt
        assert "嗑瓜子" not in prompt
        assert "坐在破椅" not in prompt
        assert "风格：银翼杀手2049风格" in prompt

    @pytest.mark.unit
    def test_character_prompt_strips_plot_bio_so_style_can_lead(self):
        """剧情简介不得压过画风：与早期能跟画风出图时的外形口径一致。"""
        style = (
            "银翼杀手2049风格，极简野蛮主义赛博朋克，只用一种颜色来统治画面，"
            "粗野主义巨物建筑，气象级的环境粒子，留白"
        )
        prompt = build_character_prompt(
            "林北",
            "本作男主，重生者。前世在末日后被黑蛇杀害，含恨而终。"
            "一朝重生回到红雾降临前90天，决心囤物资、杀仇敌。"
            "二十多岁青年男性，湿发贴额，眼神冷静。",
            style=style,
        )
        assert f"风格：{style}" in prompt
        assert prompt.index("风格：") < prompt.index("设计参考图")
        assert "二十多岁青年男性" in prompt
        assert "湿发贴额" in prompt
        assert "本作男主" not in prompt
        assert "重生者" not in prompt
        assert "杀害" not in prompt
        assert "囤物资" not in prompt
        assert "衣着崭新平整" not in prompt
        assert "禁止漂成真人" not in prompt

    @pytest.mark.unit
    def test_look_prompt_allows_damage_description(self):
        prompt = build_character_look_prompt(
            "林北",
            "前世战损",
            "脸上有血污、夹克撕破",
            base_description="青年男性，浅灰外套",
        )
        assert "衣橱造型" in prompt
        assert "干净整洁" not in prompt
        assert "衣着崭新平整" not in prompt
        assert "血污" in prompt
        assert "撕破" in prompt
        assert prompt.index("衣橱造型") < prompt.index("本造型服装与外观")

    @pytest.mark.unit
    def test_look_prompt_does_not_lock_base_clothing(self):
        """衣橱 i2i 只锁脸身，胸像与全身都必须换成本造型服装。"""
        prompt = build_character_look_prompt(
            "林北",
            "睡衣居家",
            "浅色短袖家居睡衣上衣，头发凌乱",
            base_description="青年男性，黑色战术背心",
        )
        assert "不得锁死在参考图上" in prompt
        assert "左侧胸像特写" in prompt
        assert "禁止半套换装" in prompt
        assert "不要沿用其服装或配饰" in prompt
        assert "本造型服装与外观：浅色短袖家居睡衣上衣，头发凌乱。" in prompt

    @pytest.mark.unit
    def test_look_prompt_strips_handheld_from_wardrobe_diff(self):
        prompt = build_character_look_prompt(
            "林北",
            "武装",
            "黑色战术外套，手持冲锋枪",
            base_description="青年男性，浅灰外套",
        )
        assert "黑色战术外套" in prompt
        assert "手持冲锋枪" not in prompt
        assert "冲锋枪" not in prompt

    @pytest.mark.unit
    def test_look_prompt_keeps_clothing_clause_beside_plot(self):
        """服装与剧情写在同一句时只丢剧情子句，衣服款式与颜色留给出图。"""
        prompt = build_character_look_prompt(
            "林北",
            "睡衣居家",
            "重生后的居家外观：普通家居睡衣（浅色短袖居家上衣），头发凌乱。",
            base_description="本作男主。二十多岁青年男性，浅灰外套。",
        )
        assert "普通家居睡衣（浅色短袖居家上衣）" in prompt
        assert "头发凌乱" in prompt
        assert "浅灰外套" in prompt
        assert "二十多岁青年男性" in prompt
        diff = prompt.split("本造型服装与外观：", 1)[1]
        assert "重生" not in diff
        assert "本作男主" not in prompt


@pytest.mark.unit
class TestFigureExclusion:
    """展示环境或物件的图种排除人物；画面主体本身是人物的图种不排除。"""

    # 断言完整片段而非「人物」二字：正文里的普通描述也可能出现该词，按关键词断言会误判。
    _EXCLUSION = "画面避免：出镜人物"

    def test_environment_and_object_sheets_exclude_people(self):
        assert self._EXCLUSION in build_scene_prompt("祠堂", "昏暗古朴")
        assert self._EXCLUSION in build_prop_prompt("玉佩", "古朴温润")
        assert self._EXCLUSION in build_product_prompt("护手霜", "白色管装，哑光质感")

    def test_character_and_storyboard_keep_people(self):
        # 四类资产的反向提示词各自定义而非共用，避免把人物排除项误加到主体为人物的图种上。
        assert self._EXCLUSION not in build_character_prompt("张三", "短发青年")
        assert self._EXCLUSION not in append_image_negative_tail("林清坐在窗边木桌前")


class TestVideoNegativeTail:
    @pytest.mark.unit
    def test_appends_when_missing(self):
        result = append_video_negative_tail("林清缓缓抬头")
        assert result.startswith("林清缓缓抬头")
        assert result != "林清缓缓抬头"

    @pytest.mark.unit
    def test_idempotent(self):
        once = append_video_negative_tail("林清缓缓抬头")
        twice = append_video_negative_tail(once)
        assert once == twice

    @pytest.mark.unit
    def test_handles_empty_input(self):
        assert append_video_negative_tail("")

    @pytest.mark.unit
    def test_handles_whitespace_only_input(self):
        expected = append_video_negative_tail("")
        for blank in ("   ", "\n\n", "\t \n"):
            assert append_video_negative_tail(blank) == expected


class TestImageNegativeTail:
    @pytest.mark.unit
    def test_appends_when_missing(self):
        result = append_image_negative_tail("林清坐在窗边木桌前")
        assert result.startswith("林清坐在窗边木桌前")
        assert result != "林清坐在窗边木桌前"
        assert "对镜头摆拍" in result
        assert "证件照式正脸对视镜头" in result

    @pytest.mark.unit
    def test_idempotent(self):
        once = append_image_negative_tail("林清坐在窗边木桌前")
        twice = append_image_negative_tail(once)
        assert once == twice

    @pytest.mark.unit
    def test_handles_empty_and_whitespace_input(self):
        expected = append_image_negative_tail("")
        for blank in ("", "   ", "\n\n", "\t \n"):
            assert append_image_negative_tail(blank) == expected


class TestProductFidelityTail:
    @pytest.mark.unit
    def test_appends_instruction_with_product_names(self):
        result = append_product_fidelity_tail("手持保温杯特写", ["保温杯"])
        assert result.startswith("手持保温杯特写")
        assert "「保温杯」" in result

    @pytest.mark.unit
    def test_idempotent(self):
        once = append_product_fidelity_tail("手持保温杯特写", ["保温杯"])
        twice = append_product_fidelity_tail(once, ["保温杯"])
        assert once == twice

    @pytest.mark.unit
    def test_no_products_returns_prompt_unchanged(self):
        assert append_product_fidelity_tail("氛围镜头", []) == "氛围镜头"
        # None 等同为空：早退返回原 prompt，不抛 TypeError
        assert append_product_fidelity_tail("氛围镜头", None) == "氛围镜头"

    @pytest.mark.unit
    def test_multiple_products_all_named(self):
        result = append_product_fidelity_tail("双产品同框", ["保温杯", "杯刷"])
        assert "「保温杯」" in result
        assert "「杯刷」" in result

    @pytest.mark.unit
    def test_single_string_treated_as_single_product(self):
        """误传单字符串按单产品名处理，而非逐字符迭代拼出畸形指令。"""
        result = append_product_fidelity_tail("产品特写", "保温杯")
        assert "「保温杯」" in result
        assert "「保」" not in result
