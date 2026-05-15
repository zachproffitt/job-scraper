SKILL_COLOR = "3B82F6"
REMOTE_BADGE = '<img src="https://img.shields.io/badge/Remote-22C55E?style=flat-square" align="absmiddle">'
HYBRID_BADGE = '<img src="https://img.shields.io/badge/Hybrid-F59E0B?style=flat-square" align="absmiddle">'


def skill_badge(skill: str) -> str:
    label = skill.strip().replace("-", "--").replace("_", "__").replace(" ", "_")
    label = (label
        .replace("(", "%28").replace(")", "%29")
        .replace(",", "%2C").replace("/", "%2F")
        .replace("+", "%2B").replace("#", "%23"))
    return f'<img src="https://img.shields.io/badge/{label}-{SKILL_COLOR}?style=flat-square" alt="{skill}">'
