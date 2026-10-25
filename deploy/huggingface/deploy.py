"""KBÜ MCP'yi Hugging Face Spaces'e (ücretsiz) dağıtır.

Ön koşul: `hf auth login` ile yazma yetkili bir token'la giriş yapılmış olması.

    python deploy/huggingface/deploy.py [--space kbu-mcp] [--dataset kbu-mcp-index] [--no-snapshot]

Yaptıkları:
  1. Özel (private) dataset oluşturur ve yerel indeksin anlık görüntüsünü yükler (~480 MB).
  2. Herkese açık Docker Space oluşturur, Dockerfile + README'yi yükler.
  3. Space değişkeni KBU_SNAPSHOT_REPO'yu ayarlar.
Son adım (elle, bir kez): Space → Settings → Variables and secrets → New secret: HF_TOKEN
"""

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--space", default="kbu-mcp")
    ap.add_argument("--dataset", default="kbu-mcp-index")
    ap.add_argument("--no-snapshot", action="store_true", help="Anlık görüntüyü yükleme (mevcut olanı kullan)")
    args = ap.parse_args()

    from huggingface_hub import HfApi

    api = HfApi()
    try:
        user = api.whoami()["name"]
    except Exception:
        sys.exit("Hugging Face'e giriş yapılmamış. Önce: hf auth login")
    dataset_id, space_id = f"{user}/{args.dataset}", f"{user}/{args.space}"

    # 1) İndeks anlık görüntüsü → özel dataset
    api.create_repo(dataset_id, repo_type="dataset", private=True, exist_ok=True)
    if not args.no_snapshot:
        sys.path.insert(0, str(HERE.parents[1] / "src"))
        from kbu_mcp import config, snapshot

        gz = config.DATA_DIR / "snapshot" / snapshot.FILENAME
        if not gz.exists():
            print("Anlık görüntü hazırlanıyor…")
            snapshot.export(gz)
        print(f"Yükleniyor: {gz} ({gz.stat().st_size / 1e6:.0f} MB) → {dataset_id}")
        api.upload_file(path_or_fileobj=str(gz), path_in_repo=snapshot.FILENAME, repo_id=dataset_id,
                        repo_type="dataset", commit_message="indeks anlık görüntüsü")

    # 2) Docker Space
    api.create_repo(space_id, repo_type="space", space_sdk="docker", private=False, exist_ok=True)
    for name in ("Dockerfile", "README.md"):
        api.upload_file(path_or_fileobj=str(HERE / name), path_in_repo=name, repo_id=space_id, repo_type="space",
                        commit_message=f"{name} güncellendi")

    # 3) Space değişkeni
    api.add_space_variable(space_id, "KBU_SNAPSHOT_REPO", dataset_id)

    host = space_id.replace("/", "-").replace("_", "-").lower()
    print("\nTamam.")
    print(f"  Space:        https://huggingface.co/spaces/{space_id}")
    print(f"  MCP adresi:   https://{host}.hf.space/mcp")
    print(f"  Dataset:      https://huggingface.co/datasets/{dataset_id} (özel)")
    print("\nSon adım (bir kez): Space → Settings → Variables and secrets → New secret")
    print("  Name: HF_TOKEN   Value: dataset'e okuma/yazma yetkili token")


if __name__ == "__main__":
    main()
