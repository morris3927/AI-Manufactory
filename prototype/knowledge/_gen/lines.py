"""Per-line configuration for the welding and assembly knowledge bases.

Stamping keeps its own constants in common.py / gen_sops.py / case_skeleton.py (unchanged);
this module only adds the new lines so that stamping outputs stay byte-identical.
All machine IDs, thresholds and PM cycles are 模擬資料.
"""
from __future__ import annotations

LINES = {
    "welding": {
        "prefix": "WLD",
        "name": "銲接線",
        "pm_cycle": 30,  # 模擬值：機器人工作站每月保養
        "machines": {
            "WLD-L1-RC01": {"type": "robot_mig_cell", "name": "機器人 MIG 銲接工作站 1（雙工位變位機）"},
            "WLD-L1-RC02": {"type": "robot_mig_cell", "name": "機器人 MIG 銲接工作站 2（雙工位變位機）"},
            "WLD-L1-SP01": {"type": "spot_welder", "name": "座式氣動點銲機 1（中頻）"},
            "WLD-L2-SP02": {"type": "spot_welder", "name": "座式氣動點銲機 2（中頻）"},
        },
        "machine_type_label": {
            "robot_mig_cell": "機器人 MIG（MAG）銲接工作站（六軸機器人、雙工位變位機、送絲機、清槍站、水冷銲槍、局部排氣）",
            "spot_welder": "座式氣動點銲機（中頻控制器、加壓氣缸、電極修磨器、冷卻水）",
        },
        "loto_by_type": {"robot_mig_cell": "LOTO-WELD-ROBOT-01", "spot_welder": "LOTO-SPOT-WELD-01"},
        "loto_name": {"LOTO-WELD-ROBOT-01": "機器人銲接工作站", "LOTO-SPOT-WELD-01": "點銲機"},
        "context": "台灣伺服器機殼廠的銲接線（機器人 MIG／MAG 銲接 1.0～2.0 mm SECC／SGCC 薄板支架與框架，0.9 mm 實心銲絲、Ar／CO2 混合保護氣；座式中頻點銲機點銲 SGCC 鍍鋅鋼板）",
        "terms": "銲接、銲道、銲槍、銲絲、導電嘴、噴嘴、送絲輪、送絲導管、清槍站、變位機、治具、夾爪、定位銷、接地夾、回路電纜、TCP、教導器、電極帽、電極修磨器、熔核、飛濺、氣孔、燒穿、未熔合、回燒",
        "ppe_base": ["安全鞋", "防護手套（耐熱）", "護目鏡（遮光）", "防塵口罩（銲接燻煙）"],
        "skill_levels": "「維修技術員 L1」「維修技術員 L2」「維修技術員 L3」「機器人工程師」「電氣合格人員」",
    },
    "assembly": {
        "prefix": "ASM",
        "name": "組裝線",
        "pm_cycle": 90,  # 模擬值：鎖附站每季保養
        "machines": {
            "ASM-L1-ST01": {"type": "screwdriving_station", "name": "電動起子鎖附站 1（扭力臂＋自動送釘機）"},
            "ASM-L1-ST02": {"type": "screwdriving_station", "name": "電動起子鎖附站 2（扭力臂＋自動送釘機）"},
            "ASM-L1-MA01": {"type": "manual_assembly_station", "name": "人工組裝站 1（線材、連接器）＋功能測試站"},
            "ASM-L1-MA02": {"type": "manual_assembly_station", "name": "人工組裝站 2（線材、連接器）＋功能測試站"},
        },
        "machine_type_label": {
            "screwdriving_station": "電動起子鎖附站（扭力控制式電動起子、控制器、扭力臂、自動送釘機；M3 機殼螺絲鎖入壓鉚螺母）",
            "manual_assembly_station": "人工組裝站（伺服器機殼內線材走線、連接器插接）＋下游功能測試站（裝置偵測、絕緣測試）",
        },
        "loto_by_type": {"screwdriving_station": "LOTO-ASM-TOOL-01", "manual_assembly_station": "LOTO-ASM-TOOL-01"},
        "loto_name": {"LOTO-ASM-TOOL-01": "組裝站電動工具"},
        "context": "台灣伺服器機殼廠的組裝線（電動起子鎖附站把 M3 螺絲鎖入機殼壓鉚螺母、目標扭力 0.8 N·m；人工組裝站做機殼內線材走線與連接器插接，下游功能測試站做裝置偵測與絕緣測試）",
        "terms": "鎖附、扭力、角度、浮鎖、滑牙、斜牙、起子頭、十字槽、打滑、送釘機、吸嘴、壓鉚螺母、孔牙、扭力分析儀、校正、線材、走線、彎曲半徑、束帶、護線套、連接器、卡扣、首件",
        "ppe_base": ["防割手套（鈑金銳邊）", "護目鏡", "靜電手環"],
        "skill_levels": "「作業員」「維修技術員 L1」「維修技術員 L2」「品保工程師」「電氣合格人員」",
    },
}
MTYPES_NEW = {t for cfg in LINES.values() for t in cfg["machine_type_label"]}


def machines_all() -> dict:
    return {m: v for cfg in LINES.values() for m, v in cfg["machines"].items()}
