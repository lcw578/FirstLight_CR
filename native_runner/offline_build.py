"""Local APK/resource preparation. Never launches a game or downloads game content."""

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import zipfile

PACKAGE = Path(__file__).resolve().parent


def sha256(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def supported_engine():
    return json.loads((PACKAGE / "supported_engine.json").read_text(encoding="utf-8"))


def relative_file(name):
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or "\\" in name or ":" in name:
        raise ValueError(f"Invalid resource path: {name}")
    return path


def validate_update(update: Path):
    release = supported_engine()
    if sha256(update / "fingerprint.json") != release["runtime_fingerprint_sha256"]:
        raise ValueError("Runtime update fingerprint differs from supported_engine.json")
    manifest = json.loads((PACKAGE / "data/competitive/manifest.json").read_text(encoding="utf-8"))
    # Check all required update facts before producing a build tree.
    for name, expected in manifest["source_files"].items():
        if name.startswith("runtime-update/") and sha256(update / name.removeprefix("runtime-update/")) != expected:
            raise ValueError(f"Runtime source hash mismatch: {name}")
    return manifest


def prepare(input_apk: Path, update: Path | None, workspace: Path):
    release = supported_engine()
    if sha256(input_apk) != release["apk_sha256"]:
        raise ValueError("Input APK SHA-256 differs from supported_engine.json; supply the unmodified supported APK")
    if update is None:
        raise ValueError("Local resource compilation requires --runtime-update.")
    manifest = validate_update(update)
    workspace.mkdir(parents=True, exist_ok=True)
    base = workspace / release["resource_directory"]
    with zipfile.ZipFile(input_apk) as apk:
        for item in apk.infolist():
            if item.is_dir() or not (item.filename.startswith("assets/") or item.filename == "lib/arm64-v8a/libg.so"):
                continue
            destination = base / relative_file(item.filename)
            destination.parent.mkdir(parents=True, exist_ok=True)
            with apk.open(item) as source, destination.open("wb") as target:
                shutil.copyfileobj(source, target)
    shutil.copytree(update, workspace / "runtime-update")
    for name, expected in manifest["source_files"].items():
        if sha256(workspace / name) != expected:
            raise ValueError(f"Prepared resource hash mismatch: {name}")
    print(f"Verified APK and all {len(manifest['source_files'])} pinned resource files", flush=True)


def patch(source: Path):
    game = source / "smali/com/supercell/clashroyale/GameApp.smali"
    text = game.read_text(encoding="utf-8")
    if '"crprobe"' in text:
        raise ValueError("Decoded APK is already patched; use a pristine decode of the supported input APK")
    anchor = ".method public final a()Z\n    .locals 3\n"
    if text.count(anchor) != 1:
        raise ValueError("Unsupported GameApp.a() layout")
    bootstrap = '\n    const-string v0, "crprobe"\n    invoke-static {v0}, Ljava/lang/System;->loadLibrary(Ljava/lang/String;)V\n'
    # Return false from the online-loader gate so Titan initializes libg and its renderer directly.
    bootstrap += "    const/4 v0, 0x0\n    return v0\n"
    game.write_text(text.replace(anchor, anchor + bootstrap), encoding="utf-8")
    titan = source / "smali/com/supercell/titan/GameApp.smali"
    text = titan.read_text(encoding="utf-8")
    pattern = r'(    const-string v3, "g"\s+(?:\.line \d+\s+)*invoke-static \{v3\}, Ljava/lang/System;->loadLibrary\(Ljava/lang/String;\)V)'
    text, count = re.subn(
        pattern,
        lambda m: '    const-string v3, "scid_sdk"\n'
        "    invoke-static {v3}, Ljava/lang/System;->loadLibrary(Ljava/lang/String;)V\n\n"
        + m[0]
        + '\n\n    const-string v3, "crprobe"\n    invoke-static {v3}, Ljava/lang/System;->loadLibrary(Ljava/lang/String;)V\n    invoke-static {}, Lcom/supercell/titan/GameApp;->nativeBootstrapProbe()V',
        text,
    )
    if count != 1 or text.count("# direct methods") != 1:
        raise ValueError("Unsupported Titan native-library bootstrap layout")
    text = text.replace(
        "# direct methods", "# direct methods\n.method private static native nativeBootstrapProbe()V\n.end method\n"
    )
    titan.write_text(text, encoding="utf-8")
    add_play_review_stubs(source)


def add_play_review_stubs(source: Path):
    """Add no-op Play Core review classes so libg's FindClass/GetStaticMethodID
    calls at battle transitions succeed instead of aborting the process.

    The engine (libg.so) resolves com/google/android/play/core/review/
    ReviewManagerFactory via JNI when the rate-app prompt triggers. The private
    server APK ships without Google Play components, and the patched startup
    path bypasses the private-server framework that previously kept the feature
    dormant, so the lookup hits a null class and CheckJNI kills the game. The
    stubs mirror the public Play Core API surface used by the engine:
    ReviewManagerFactory.create(), ReviewManager.requestReviewFlow() /
    launchReviewFlow(), the Task handle with addOnCompleteListener /
    isSuccessful / getResult(), and the listener interface.
    """
    review_dir = source / "smali/com/google/android/play/core/review"
    tasks_dir = source / "smali/com/google/android/gms/tasks"
    review_dir.mkdir(parents=True, exist_ok=True)
    tasks_dir.mkdir(parents=True, exist_ok=True)

    (review_dir / "ReviewInfo.smali").write_text(
        ".class public Lcom/google/android/play/core/review/ReviewInfo;\n"
        ".super Ljava/lang/Object;\n"
        "\n"
        "\n"
        "# direct methods\n"
        ".method public constructor <init>()V\n"
        "    .locals 0\n"
        "\n"
        "    invoke-direct {p0}, Ljava/lang/Object;-><init>()V\n"
        "\n"
        "    return-void\n"
        ".end method\n",
        encoding="utf-8",
    )

    (review_dir / "ReviewManager.smali").write_text(
        ".class public interface abstract Lcom/google/android/play/core/review/ReviewManager;\n"
        ".super Ljava/lang/Object;\n"
        "\n"
        "\n"
        "# virtual methods\n"
        ".method public abstract launchReviewFlow(Landroid/app/Activity;Lcom/google/android/play/core/review/ReviewInfo;)Lcom/google/android/gms/tasks/Task;\n"
        ".end method\n"
        "\n"
        ".method public abstract requestReviewFlow()Lcom/google/android/gms/tasks/Task;\n"
        ".end method\n",
        encoding="utf-8",
    )

    (review_dir / "ReviewManagerFactory.smali").write_text(
        ".class public Lcom/google/android/play/core/review/ReviewManagerFactory;\n"
        ".super Ljava/lang/Object;\n"
        "\n"
        "\n"
        "# direct methods\n"
        ".method public constructor <init>()V\n"
        "    .locals 0\n"
        "\n"
        "    invoke-direct {p0}, Ljava/lang/Object;-><init>()V\n"
        "\n"
        "    return-void\n"
        ".end method\n"
        "\n"
        ".method public static create(Landroid/content/Context;)Lcom/google/android/play/core/review/ReviewManager;\n"
        "    .locals 1\n"
        "\n"
        "    new-instance v0, Lcom/google/android/play/core/review/StubsReviewManager;\n"
        "\n"
        "    invoke-direct {v0}, Lcom/google/android/play/core/review/StubsReviewManager;-><init>()V\n"
        "\n"
        "    return-object v0\n"
        ".end method\n",
        encoding="utf-8",
    )

    (review_dir / "StubsReviewManager.smali").write_text(
        ".class public Lcom/google/android/play/core/review/StubsReviewManager;\n"
        ".super Ljava/lang/Object;\n"
        '.source "StubsReviewManager.java"\n'
        "\n"
        "# interfaces\n"
        ".implements Lcom/google/android/play/core/review/ReviewManager;\n"
        "\n"
        "\n"
        "# direct methods\n"
        ".method public constructor <init>()V\n"
        "    .locals 0\n"
        "\n"
        "    invoke-direct {p0}, Ljava/lang/Object;-><init>()V\n"
        "\n"
        "    return-void\n"
        ".end method\n"
        "\n"
        "\n"
        "# virtual methods\n"
        ".method public launchReviewFlow(Landroid/app/Activity;Lcom/google/android/play/core/review/ReviewInfo;)Lcom/google/android/gms/tasks/Task;\n"
        "    .locals 1\n"
        "\n"
        "    new-instance v0, Lcom/google/android/gms/tasks/StubsTask;\n"
        "\n"
        "    invoke-direct {v0}, Lcom/google/android/gms/tasks/StubsTask;-><init>()V\n"
        "\n"
        "    return-object v0\n"
        ".end method\n"
        "\n"
        ".method public requestReviewFlow()Lcom/google/android/gms/tasks/Task;\n"
        "    .locals 1\n"
        "\n"
        "    new-instance v0, Lcom/google/android/gms/tasks/StubsTask;\n"
        "\n"
        "    invoke-direct {v0}, Lcom/google/android/gms/tasks/StubsTask;-><init>()V\n"
        "\n"
        "    return-object v0\n"
        ".end method\n",
        encoding="utf-8",
    )

    (tasks_dir / "Task.smali").write_text(
        ".class public abstract Lcom/google/android/gms/tasks/Task;\n"
        ".super Ljava/lang/Object;\n"
        "\n"
        "\n"
        "# direct methods\n"
        ".method public constructor <init>()V\n"
        "    .locals 0\n"
        "\n"
        "    invoke-direct {p0}, Ljava/lang/Object;-><init>()V\n"
        "\n"
        "    return-void\n"
        ".end method\n"
        "\n"
        "\n"
        "# virtual methods\n"
        ".method public addOnCompleteListener(Lcom/google/android/gms/tasks/OnCompleteListener;)Lcom/google/android/gms/tasks/Task;\n"
        "    .locals 1\n"
        "\n"
        "    new-instance v0, Lcom/google/android/gms/tasks/StubsTask;\n"
        "\n"
        "    invoke-direct {v0}, Lcom/google/android/gms/tasks/StubsTask;-><init>()V\n"
        "\n"
        "    return-object v0\n"
        ".end method\n"
        "\n"
        ".method public isSuccessful()Z\n"
        "    .locals 1\n"
        "\n"
        "    const/4 v0, 0x0\n"
        "\n"
        "    return v0\n"
        ".end method\n"
        "\n"
        ".method public getResult()Ljava/lang/Object;\n"
        "    .locals 1\n"
        "\n"
        "    const/4 v0, 0x0\n"
        "\n"
        "    return-object v0\n"
        ".end method\n",
        encoding="utf-8",
    )

    (tasks_dir / "OnCompleteListener.smali").write_text(
        ".class public interface abstract Lcom/google/android/gms/tasks/OnCompleteListener;\n"
        ".super Ljava/lang/Object;\n"
        "\n"
        "\n"
        "# virtual methods\n"
        ".method public abstract onComplete(Lcom/google/android/gms/tasks/Task;)V\n"
        ".end method\n",
        encoding="utf-8",
    )

    (tasks_dir / "StubsTask.smali").write_text(
        ".class public Lcom/google/android/gms/tasks/StubsTask;\n"
        ".super Lcom/google/android/gms/tasks/Task;\n"
        '.source "StubsTask.java"\n'
        "\n"
        "\n"
        "# direct methods\n"
        ".method public constructor <init>()V\n"
        "    .locals 0\n"
        "\n"
        "    invoke-direct {p0}, Lcom/google/android/gms/tasks/Task;-><init>()V\n"
        "\n"
        "    return-void\n"
        ".end method\n",
        encoding="utf-8",
    )


def verify(apk: Path, probe: Path):
    release = supported_engine()
    with zipfile.ZipFile(apk) as archive:
        for name, expected in (
            ("lib/arm64-v8a/libg.so", release["libg_sha256"]),
            ("lib/arm64-v8a/libcrprobe.so", sha256(probe)),
        ):
            if hashlib.sha256(archive.read(name)).hexdigest() != expected:
                raise ValueError(f"Output APK payload mismatch: {name}")
        if any(name.startswith("assets/firstlight/") for name in archive.namelist()):
            raise ValueError("Offline APK must reuse device resources, not bundle an update")
        # Smali stubs compile into classes*.dex; confirm the review stubs survived.
        dexes = [n for n in archive.namelist() if n.endswith(".dex")]
        stub_ok = any(
            b"com/google/android/play/core/review/ReviewManagerFactory" in archive.read(dex_name)
            for dex_name in dexes
        )
        if not stub_ok:
            raise ValueError("Play review stub classes missing from built APK")
    report = {
        "schema": "firstlight-offline-build.v1",
        "apk_sha256": sha256(apk),
        "probe_sha256": sha256(probe),
        "resources": "existing-device-update",
        "supported_engine": release,
    }
    apk.with_suffix(".build.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print("Verified output payload and wrote build receipt", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("check-input")
    p.add_argument("--input-apk", type=Path, required=True)
    p.add_argument("--allow-input-override", action="store_true",
                   help="Accept a redistributed APK whose ZIP entries match the supported engine "
                        "but whose whole-file hash differs (Null's re-signed variant)")
    p = sub.add_parser("prepare")
    p.add_argument("--input-apk", type=Path, required=True)
    p.add_argument("--runtime-update", type=Path)
    p.add_argument("--workspace", type=Path, required=True)
    p = sub.add_parser("patch")
    p.add_argument("--source", type=Path, required=True)
    p = sub.add_parser("verify")
    p.add_argument("--apk", type=Path, required=True)
    p.add_argument("--probe", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "check-input":
        if sha256(args.input_apk) == supported_engine()["apk_sha256"]:
            print("Verified original APK", flush=True)
        elif args.allow_input_override:
            print("Original APK SHA-256 differs from supported_engine.json; "
                  "override accepted after verifying libg.so matches supported_engine.json", flush=True)
            with zipfile.ZipFile(args.input_apk) as archive:
                if hashlib.sha256(archive.read("lib/arm64-v8a/libg.so")).hexdigest() != supported_engine()["libg_sha256"]:
                    raise ValueError("Input APK engine library differs from supported_engine.json")
        else:
            raise ValueError("Input APK SHA-256 differs from supported_engine.json")
    elif args.command == "prepare":
        prepare(args.input_apk, args.runtime_update, args.workspace)
    elif args.command == "patch":
        patch(args.source)
    else:
        verify(args.apk, args.probe)


if __name__ == "__main__":
    main()
