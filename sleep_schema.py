"""OpenAI function calling schema — maps to Notion 醒后数据记录."""

WAKEUP_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "log_wakeup_record",
            "description": (
                "记录醒后状态数据：清醒度、疲劳度、用药（替洛利生/过敏药）、"
                "咖啡因摄入、特殊情况等。"
                "当用户描述睡眠质量、早起感受、困倦程度、用药或咖啡因摄入时自动调用。"
                "注意：咖啡因摄入（如咖啡、茶、奶茶等）请填入 caffeine/caffeine_time 字段，"
                "不要填入 notes。替洛利生/过敏药信息请填入对应字段，不要填入 notes。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "date": {
                        "type": "string",
                        "description": "记录日期，格式 YYYY-MM-DD。"
                        "用户说'今天'用当前日期，'昨天'用当前日期减1天。",
                    },
                    "alertness": {
                        "type": "integer",
                        "description": "清醒度 0-5，0=完全不清醒，5=非常清醒。"
                        "根据用户描述的睡眠质量和当前状态推断。",
                    },
                    "fatigue": {
                        "type": "integer",
                        "description": "疲劳度 0-5，0=完全不累，5=极度疲惫。"
                        "根据用户描述的疲劳感受推断。",
                    },
                    "pitolisant": {
                        "type": "boolean",
                        "description": "是否服用了替洛利生。用户明确提到替洛利生或 pitolisant 时设为 true。",
                    },
                    "pitolisant_time": {
                        "type": "array",
                        "description": "替洛利生服用时段。仅当 pitolisant=true 时填写。可多选。",
                        "items": {
                            "type": "string",
                            "enum": ["上午", "中午", "下午"],
                        },
                    },
                    "caffeine": {
                        "type": "boolean",
                        "description": "是否摄入了咖啡因。任何含咖啡因的饮品或食物（咖啡、拿铁、美式、奶茶、茶、可乐、能量饮料等）都算。"
                        "用户说'喝了咖啡/奶茶/茶/可乐'等，必须设为 true。"
                        "用户明确说'没喝咖啡/没喝茶'等，设为 false。"
                        "重要：不要将咖啡因信息填入 notes 字段，请使用此字段。",
                    },
                    "caffeine_time": {
                        "type": "array",
                        "description": "咖啡因摄入时段。仅当 caffeine=true 时填写。可多选。",
                        "items": {
                            "type": "string",
                            "enum": ["上午", "中午", "下午", "晚上"],
                        },
                    },
                    "allergy_med": {
                        "type": "boolean",
                        "description": "是否服用了过敏药。用户明确提到过敏药/抗过敏时设为 true。",
                    },
                    "allergy_med_time": {
                        "type": "array",
                        "description": "过敏药服用时段。仅当 allergy_med=true 时填写。可多选。",
                        "items": {
                            "type": "string",
                            "enum": ["上午", "中午", "下午", "晚上"],
                        },
                    },
                    "notes": {
                        "type": "string",
                        "description": "特殊情况备注，如多梦、中途醒来、身体不适、情绪状态、"
                        "午睡情况、酒精摄入等值得记录的上下文。"
                        "注意：咖啡因、替洛利生、过敏药等信息有专门的字段，不要写在这里。",
                    },
                },
                "required": ["date"],
            },
        }
    }
]
