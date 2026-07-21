# plugins/scan_info/scan_info.py
import os
import re
import blinker
from docutils import nodes
from docutils.parsers.rst import roles, directives
from docutils.parsers.rst import Directive
from nikola.plugin_categories import RestExtension


# Папка с галереями (от корня сайта / рабочей директории сборки)
SCANS_DIR = "scans"
LIBRARY_RST = os.path.join("pages", "library.rst")

_scan_catalog_cache = {"mtime": None, "data": {}}


def format_size_bytes(path):
    try:
        size = os.path.getsize(path)
        return f"{size / (1024 * 1024):.1f} МБ"
    except OSError:
        return "—"


def make_link(href, text, css_class=""):
    cls = f' class="{css_class}"' if css_class else ""
    return f'<a href="{href}"{cls}>{text}</a>'


def build_download_links_html(book_dirname):
    """Return HTML for PDF/DJVU download links, or empty string."""
    download_links = []
    pdf_path_fs = os.path.join(SCANS_DIR, f"{book_dirname}.pdf")
    djvu_path_fs = os.path.join(SCANS_DIR, f"{book_dirname}.djvu")

    if os.path.isfile(pdf_path_fs):
        size = format_size_bytes(pdf_path_fs)
        href = f"/{pdf_path_fs.replace(os.path.sep, '/')}"
        download_links.append(
            make_link(
                href,
                f"<i class='bi bi-file-earmark-pdf' style='color:#c00;'></i> Скачать PDF ({size})",
                "download pdf",
            )
        )

    if os.path.isfile(djvu_path_fs):
        size = format_size_bytes(djvu_path_fs)
        href = f"/{djvu_path_fs.replace(os.path.sep, '/')}"
        download_links.append(
            make_link(
                href,
                f"<i class='bi bi-file-earmark' style='color:#c00;'></i> Скачать DJVU ({size})",
                "download djvu",
            )
        )

    return "<br />".join(download_links)


def parse_library_rst(path=LIBRARY_RST):
    """Parse .. scan:: entries from library.rst, keyed by :path:."""
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return {}

    if _scan_catalog_cache["mtime"] == mtime:
        return _scan_catalog_cache["data"]

    catalog = {}
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return {}

    i = 0
    while i < len(lines):
        match = re.match(r"\.\.\s+scan::\s+(.+?)\s*$", lines[i])
        if match:
            title = match.group(1).strip()
            book_path = None
            desc = None
            i += 1
            while i < len(lines) and re.match(r"\s+:", lines[i]):
                opt = lines[i].strip()
                if opt.startswith(":path:"):
                    book_path = opt[len(":path:"):].strip()
                elif opt.startswith(":desc:"):
                    desc = opt[len(":desc:"):].strip()
                i += 1
            if book_path:
                catalog[book_path] = {"title": title, "desc": desc or ""}
            continue
        i += 1

    _scan_catalog_cache["mtime"] = mtime
    _scan_catalog_cache["data"] = catalog
    return catalog


def apply_scan_metadata(context, book_dirname):
    """Apply title and description from library.rst to gallery context."""
    info = parse_library_rst().get(book_dirname)
    if not info:
        return

    context["scan_title"] = info["title"]
    if info["desc"]:
        context["scan_desc"] = info["desc"]
        context["title"] = f"{info['title']}: {info['desc']}"
    else:
        context["title"] = info["title"]

    crumbs = context.get("crumbs")
    if crumbs:
        link, _text = crumbs[-1]
        context["crumbs"] = crumbs[:-1] + [[link, info["title"]]]


def gallery_context_filler(context, template_name):
    """Add scan metadata and download links to individual gallery pages."""
    if template_name != "gallery.tmpl":
        return
    gallery_path = context.get("gallery_path", "")
    prefix = SCANS_DIR + os.sep
    if not gallery_path.startswith(prefix):
        return
    book_dirname = os.path.basename(gallery_path)
    apply_scan_metadata(context, book_dirname)
    html = build_download_links_html(book_dirname)
    if html:
        context["scan_downloads_html"] = html


def _patch_gallery_tasks(sender):
    """Rebuild gallery pages when library.rst changes."""
    if not os.path.isfile(LIBRARY_RST):
        return

    from nikola.plugins.task.galleries import Galleries

    original_gen_tasks = Galleries.gen_tasks

    def gen_tasks_with_library_dep(self):
        for task in original_gen_tasks(self):
            if task.get("basename") == "render_galleries" and "file_dep" in task:
                task["file_dep"] = list(task["file_dep"]) + [LIBRARY_RST]
            yield task

    Galleries.gen_tasks = gen_tasks_with_library_dep


# ---- реализация роли (кошка) ----
def annotate_scan(role, rawtext, text, lineno, inliner, options={}, content=[]):
    # Простая роль: :scan:`текст` -> <i>текст</i>
    html = f"<i>{text}</i>"
    return [nodes.raw("", html, format="html")], []


# ---- реализация директивы ----
class ScanDirective(Directive):
    has_content = True
    required_arguments = 1
    optional_arguments = 0
    # разрешаем пробелы в названии (последний аргумент)
    final_argument_whitespace = True
    option_spec = {
        "path": directives.unchanged,  # например Quorum_64
        "desc": directives.unchanged,
    }

    def run(self):
        book_title = self.arguments[0].strip()
        book_dirname = self.options.get("path", "").strip()
        desc = self.options.get("desc", "").strip()

        if not book_dirname:
            # ошибаемся, если опция path не указана
            return [self.state_machine.reporter.error(
                "scan directive: missing ':path:' option (e.g. :path: Quorum_64).",
                line=self.lineno
            )]

        # Путь к галерее (относительно рабочей директории сборки)
        gallery_path = os.path.join(SCANS_DIR, book_dirname)

        # Если галереи нет — падать с ошибкой (как ты просил)
        if not os.path.isdir(gallery_path):
            return [self.state_machine.reporter.error(
                f"scan directive: gallery not found: '{gallery_path}'",
                line=self.lineno
            )]

        # Найти первую картинку в галерее (по алфавиту)
        thumb_rel = None
        try:
            for fname in sorted(os.listdir(gallery_path)):
                if fname.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
                    # относительная ссылка от корня сайта (начинаем с '/')
                    thumb_rel = f"/{os.path.join(gallery_path, fname).replace(os.path.sep, '/').replace('.jpg', '.thumbnail.jpg')}"
                    break
        except OSError:
            # если чтение каталога неожиданно упало
            return [self.state_machine.reporter.error(
                f"scan directive: cannot read gallery directory '{gallery_path}'",
                line=self.lineno
            )]

        downloads_html = build_download_links_html(book_dirname)

        # ссылка на галерею (директория должна существовать)
        gallery_href = f"/{gallery_path.replace(os.path.sep, '/')}/"

        # thumbnail HTML (если нет картинок, показываем пустой контейнер)
        thumb_html = (f'<img src="{thumb_rel}" alt="{book_title}" class="scan-thumb" />'
                      if thumb_rel else '<div class="scan-thumb empty-thumb"></div>')

        # Формируем HTML-блок: левый столб — thumb + title, правый — описание + ссылки
        html = f"""
<div class="scan-entry" style="display:flex;gap:1rem;align-items:flex-start;margin:1rem 0;">
  <div class="scan-left" style="flex:0 0 180px;text-align:center;">
    <a href="{gallery_href}">{thumb_html}</a>
  </div>
  <div class="scan-right" style="flex:1;">
    <h3>{make_link(gallery_href, book_title, "scan-title")}</h3>
    <div class="scan-desc">{desc}</div>
    <div class="scan-links" style="margin-top:0.5em;">
      {make_link(gallery_href, "<i class='bi bi-search' style='color:#00c;'></i> Просмотреть", 'view-gallery')}<br />
      {downloads_html if downloads_html else ''}
    </div>
  </div>
</div>
"""
        return [nodes.raw("", html, format="html")]


# Регистрируем роль и директиву при импорте (docutils увидит их вовремя)
roles.register_canonical_role("scan", annotate_scan)
directives.register_directive("scan", ScanDirective)


# Плагин Nikola (интеграция)
class Plugin(RestExtension):
    name = "scan_info"

    def set_site(self, site):
        self.site = site
        site.config["GLOBAL_CONTEXT_FILLER"].append(gallery_context_filler)
        blinker.signal("initialized").connect(self._on_initialized)
        return super().set_site(site)

    def _on_initialized(self, sender, **kwargs):
        _patch_gallery_tasks(sender)
