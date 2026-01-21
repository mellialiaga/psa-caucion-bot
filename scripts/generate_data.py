from datetime import datetime, timezone
from pathlib import Path
import json

OUT = Path("docs") / "data" / "latest.json"

def main():
    OUT.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "dummy",
        "term": "1D",
        "tna": 30.0,
        "tea": None,
        "quality": "placeholder",
        "note": "Primer dato generado correctamente",
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"OK → generado {OUT}")

if __name__ == "__main__":
    main()
