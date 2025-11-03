# plugins/scan_info/scan_info.py
import os
from docutils import nodes
from docutils.parsers.rst import roles, directives
from docutils.parsers.rst import Directive
from nikola.plugin_categories import RestExtension


# Папка с галереями (от корня сайта / рабочей директории сборки)
SCANS_DIR = "scans"


def format_size_bytes(path):
    try:
        size = os.path.getsize(path)
        return f"{size / (1024 * 1024):.1f} МБ"
    except OSError:
        return "—"


def make_link(href, text, css_class=""):
    cls = f' class="{css_class}"' if css_class else ""
    return f'<a href="{href}"{cls}>{text}</a>'


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

        # Проверяем наличие PDF / DJVU файлов рядом (scans/Quorum_64.pdf и .djvu)
        pdf_path_fs = os.path.join(SCANS_DIR, f"{book_dirname}.pdf")
        djvu_path_fs = os.path.join(SCANS_DIR, f"{book_dirname}.djvu")

        download_links = []
        if os.path.isfile(pdf_path_fs):
            size = format_size_bytes(pdf_path_fs)
            href = f"/{pdf_path_fs.replace(os.path.sep, '/')}"
            download_links.append(make_link(href, f"<i class='bi bi-file-earmark-pdf' style='color:#c00;'></i> Скачать PDF ({size})", "download pdf"))

        if os.path.isfile(djvu_path_fs):
            size = format_size_bytes(djvu_path_fs)
            href = f"/{djvu_path_fs.replace(os.path.sep, '/')}"
            download_links.append(make_link(href, f"<i class='bi bi-file-earmark' style='color:#c00;'></i> Скачать DJVU ({size})", "download djvu"))

        # ссылка на галерею (директория должна существовать)
        gallery_href = f"/{gallery_path.replace(os.path.sep, '/')}/"

        # thumbnail HTML (если нет картинок, показываем пустой контейнер)
        thumb_html = (f'<img src="{thumb_rel}" alt="{book_title}" class="scan-thumb" />'
                      if thumb_rel else '<div class="scan-thumb empty-thumb"></div>')

        downloads_html = " ".join(download_links) if download_links else ""

        # Формируем HTML-блок: левый столб — thumb + title, правый — описание + ссылки
        html = f"""
<div class="scan-entry" style="display:flex;gap:1rem;align-items:flex-start;margin:1rem 0;">
  <div class="scan-left" style="flex:0 0 180px;text-align:center;">
    <a href="{gallery_href}">{thumb_html}</a>
  </div>
  <div class="scan-right" style="flex:1;">
    <h3>{book_title}</h3>
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
        return super().set_site(site)
