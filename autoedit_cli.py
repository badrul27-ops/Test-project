"""Command-line version (useful for batch jobs).

  python autoedit_cli.py analyze --voice vo.mp3 --media images_folder --out myvideo.json
  python autoedit_cli.py render myvideo.json
"""
import argparse
import os
import sys

from autoedit import project as prj
from autoedit.render import export_package, render_all


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("analyze")
    a.add_argument("--voice", required=True)
    a.add_argument("--media", default="")
    a.add_argument("--script", default="")
    a.add_argument("--music", default="")
    a.add_argument("--sfx", default="")
    a.add_argument("--output-dir", default="")
    a.add_argument("--name", default="")
    a.add_argument("--preset", default="Documentary")
    a.add_argument("--captions", default="Modern (word highlight)")
    a.add_argument("--scene-mode", choices=["smart", "auto", "match_media", "timestamps", "script_cues"],
                   default="smart")
    a.add_argument("--model", default="small")
    a.add_argument("--language", default="")
    a.add_argument("--aspect", choices=["16:9", "9:16", "1:1"], default="16:9", help="ONE video in this shape")
    a.add_argument("--resolution", choices=["Draft", "HD", "Full HD", "1440", "4K-class"], default="Full HD")
    a.add_argument("--fps", type=int, choices=[24, 25, 30, 60], default=30)
    a.add_argument("--enc-quality", choices=["Draft", "Standard", "High"], default="Standard")
    a.add_argument("--batch-formats", default="", help="extra shapes to render too, e.g. 9:16,1:1 (batch export)")
    a.add_argument("--formats", default="", help=argparse.SUPPRESS)        # old option: first = aspect, rest = batch
    a.add_argument("--prompts", default="", help="image prompts/descriptions (csv/json/txt) for meaning matching")
    a.add_argument("--scene-map", default="", help="scene map csv/json (scene_id, asset_filename, ...)")
    a.add_argument("--sfx-level", default="Normal")
    a.add_argument("--atmosphere", default="None")
    a.add_argument("--out", required=True)
    r = sub.add_parser("render")
    r.add_argument("project")
    r.add_argument("--package", action="store_true", help="export editor package instead of MP4")
    args = ap.parse_args()

    if args.cmd == "analyze":
        p = prj.new_project()
        p.update(voiceover=os.path.abspath(args.voice), media_dir=os.path.abspath(args.media) if args.media else "",
                 script=args.script, music=args.music, sfx_dir=args.sfx,
                 output_dir=args.output_dir or os.path.dirname(os.path.abspath(args.voice)),
                 name=args.name or os.path.splitext(os.path.basename(args.voice))[0])
        batch = [f for f in args.batch_formats.split(",") if f]
        aspect = args.aspect
        if args.formats:
            old = [f for f in args.formats.split(",") if f]
            aspect, batch = old[0], old[1:]
        p["settings"].update(preset=args.preset, caption_style=args.captions, scene_mode=args.scene_mode,
                             whisper_model=args.model, language=args.language, aspect=aspect,
                             resolution=args.resolution, fps=args.fps, enc_quality=args.enc_quality,
                             batch_export=bool(batch), batch_formats=batch,
                             sfx_level=args.sfx_level, atmosphere=args.atmosphere)
        if args.prompts:
            from autoedit.asset_info import import_prompts
            n, probs = import_prompts(p, args.prompts, prj.project_media(p))
            print(f"{n} image descriptions imported", *probs, sep="\n  ")
        if args.scene_map:
            from autoedit.planner import load_scene_map
            rows, probs = load_scene_map(args.scene_map, prj.project_media(p))
            p["scene_map"] = rows
            print(f"scene map: {len(rows)} rows", *probs, sep="\n  ")
        prj.analyze(p, progress=lambda x: print(f"  {x * 100:.0f}%", end="\r"))
        prj.save(p, args.out)
        for i, s in enumerate(p["scenes"]):
            print(f"{i + 1:3d} {s['start']:7.2f}-{s['end']:7.2f} {os.path.basename(s['media']):24s} "
                  f"{s.get('status', ''):9s} {s['motion']:9s} ->{s['transition']:9s} {s['text'][:50]}")
        print("Saved", args.out)
    else:
        p = prj.load(args.project)
        if args.package:
            print(export_package(p, progress=lambda x, m: print(f"  {m}      ", end="\r")))
        else:
            outs = render_all(p, progress=lambda x, m: print(f"  {m}      ", end="\r"))
            print("\n".join(outs))


if __name__ == "__main__":
    sys.exit(main())
