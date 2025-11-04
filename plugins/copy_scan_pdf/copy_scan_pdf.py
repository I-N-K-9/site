import glob
import os
import blinker
import shutil
from nikola import utils
from nikola.plugin_categories import SignalHandler


class Plugin(SignalHandler):
    name = "copy_scan_pdf"

    def set_site(self, site):
        self.site = site
        super().set_site(site)
        blinker.signal("initialized").connect(self._on_initialized)
        print("[copy_scan_pdf] connected to 'initialized'")

    def _on_initialized(self, sender, **kwargs):
        scans_root = "scans"
        pdfs = glob.glob(os.path.join(scans_root, "**", "*.pdf"), recursive=True)
        djvus = glob.glob(os.path.join(scans_root, "**", "*.djvu"), recursive=True)
        files = pdfs + djvus
        if not files:
            print("[copy_scan_pdf] no pdf/djvu found")
            return

        out_root = sender.config.get("OUTPUT_FOLDER", sender.config.get("OUTPUT_DIR", "output"))
        copied, skipped = 0, 0

        for src in files:
            dst = os.path.join(out_root, src.replace(os.path.sep, "/"))
            dst_dir = os.path.dirname(dst)
            os.makedirs(dst_dir, exist_ok=True)

            try:
                if not os.path.exists(dst) or os.path.getmtime(src) > os.path.getmtime(dst):
                    shutil.copy2(src, dst)
                    copied += 1
                    print(f"[copy_scan_pdf] copied {src} -> {dst}")
                else:
                    skipped += 1
            except Exception as e:
                print(f"[copy_scan_pdf] failed to copy {src}: {e}")

        print(f"[copy_scan_pdf] copied {copied} file(s), skipped {skipped} (up-to-date)")
