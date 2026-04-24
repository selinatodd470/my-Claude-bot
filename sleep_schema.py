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
                        "description": "是否摄入了咖啡因（咖啡、茶、可乐、能量饮料等）。"
                        "用户提到喝了咖啡/奶茶/茶等含咖啡因饮品则设为 true。"
                        "用户明确说没喝则设为 false。",
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
                        "午睡情况、酒精摄入等值得记录的上下文。",
                    },
                },
                "required": ["date"],
            },
        }
    }
]
