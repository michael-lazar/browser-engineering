import socket
import ssl
import os
import re
import gzip
import time
import tkinter
import tkinter.font


# DEFAULT_URL = "https://browser.engineering/http.html"
# DEFAULT_URL = "https://mozz.us/"
# DEFAULT_URL = "http://browser.engineering/redirect"
DEFAULT_URL = "file://" + os.path.abspath(os.path.join(os.path.dirname(__file__), "index.html"))
# DEFAULT_URL = "https://www.zggdwx.com/xiyou/1.html"

WIDTH, HEIGHT = 800, 600
HSTEP, VSTEP = 13, 18
SCROLL_STEP = 100
FONT_SIZE = 18

request_cache = {}


def request(url=DEFAULT_URL, request_headers=None, redirects=0):
    if redirects > 5:
        raise RuntimeError("Max redirects exceeded")

    if url.startswith("data:"):
        content_type, body = url[len("data:"):].split(",", 1)
        headers = {"Content-Type": content_type}
        return headers, body

    cached_request = request_cache.get(url)
    if cached_request and cached_request['exp'] < time.time():
        return cached_request['headers'], cached_request['body']

    scheme, authority = url.split("://", 1)
    assert scheme in ["http", "https", "file"]
    host, path = authority.split("/", 1)
    path = "/" + path
    port = 80 if scheme == "http" else 443
    if ":" in host:
        host, port = host.split(":", 1)
        port = int(port)

    if scheme == "file":
        with open(path, "r") as fp:
            body = fp.read()
        return {}, body

    with socket.socket(family=socket.AF_INET, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP) as s:
        s.connect((host, port))
        if scheme == "https":
            ctx = ssl.create_default_context()
            s = ctx.wrap_socket(s, server_hostname=host)

        request_headers = request_headers or {}
        request_headers.setdefault("Host", host)
        request_headers.setdefault("Connection", "close")
        request_headers.setdefault("User-Agent", "mozz-test")
        request_headers.setdefault("Accept-Encoding", "gzip")

        request_body = f"GET {path} HTTP/1.1\r\n"
        for key, value in request_headers.items():
            request_body += f"{key}: {value}\r\n"
        request_body += "\r\n"

        s.send(request_body.encode())

        response = s.makefile("rb", newline="\r\n")

        status_line = response.readline().decode("ascii")
        print(status_line)
        version, status, explanation = status_line.split(" ", 2)

        headers = {}
        while True:
            line = response.readline().decode("ascii")
            if line == "\r\n":
                break
            header, value = line.split(":", 1)
            headers[header.lower()] = value.strip()

        if status.startswith("3"):
            location = headers['location']
            if location.startswith("/"):
                redirect_url = f"{scheme}://{host}{location}"
            else:
                redirect_url = location
            return request(redirect_url, redirects=redirects+1)

        assert status == "200", "{}: {}".format(status, explanation)

        body = response.read()
        if headers.get('content-encoding') == "gzip":
            if headers.get('transfer-encoding') == "chunked":
                size_hex, body = body.split(b"\r\n", 1)
                size = int(size_hex, 16)
                buffer = b""
                while size != 0:
                    buffer += gzip.decompress(body[:size])
                    body = body[size+2:]
                    size_hex, body = body.split(b"\r\n", 1)
                    size = int(size_hex, 16)
                body = buffer
            else:
                body = gzip.decompress(body)

        body = body.decode("utf-8")

        cache_control = headers.get('cache-control', '')
        if cache_control.startswith("max-age="):
            max_age = int(cache_control[len("max-age="):])
            request_cache[url] = {"exp": time.time() + max_age, "headers": headers, "body": body}

    return headers, body


class Text:
    def __init__(self, text, parent):
        self.raw_text = text
        self.text = self.clean_text(text)
        self.children = []
        self.parent = parent

    def __repr__(self):
        return repr(self.raw_text)

    def clean_text(self, text):
        text = re.sub("&lt;", "<", text)
        text = re.sub("&gt;", ">", text)
        text = re.sub("&ndash;", "-", text)
        text = re.sub("&copy;", "©", text)
        text = re.sub("&amp;", "&", text)
        return text


class Element:
    def __init__(self, tag, attributes, parent):
        self.tag = tag
        self.attributes = attributes
        self.children = []
        self.parent = parent

    def __repr__(self):
        return f"<{self.tag}>"


class HTMLParser:
    SELF_CLOSING_TAGS = [
        "area", "base", "br", "col", "embed", "hr", "img", "input",
        "link", "meta", "param", "source", "track", "wbr",
    ]
    HEAD_TAGS = [
        "base", "basefont", "bgsound", "noscript",
        "link", "meta", "title", "style", "script",
    ]

    def __init__(self, body):
        self.body = body
        self.unfinished = []

    def parse(self):
        text = ""
        in_tag = False
        for c in self.body:
            if c == "<":
                in_tag = True
                if text:
                    self.add_text(text)
                text = ""
            elif c == ">":
                in_tag = False
                self.add_tag(text)
                text = ""
            else:
                text += c
        if not in_tag and text:
            self.add_text(text)
        return self.finish()

    def add_text(self, text):
        if text.isspace():
            return

        self.implicit_tags(None)

        parent = self.unfinished[-1]
        node = Text(text, parent)
        parent.children.append(node)

    def add_tag(self, tag):
        tag, attributes = self.get_attributes(tag)
        if tag.startswith("!"):
            return

        self.implicit_tags(tag)

        if tag.startswith("/"):
            if len(self.unfinished) == 1:
                return
            node = self.unfinished.pop()
            parent = self.unfinished[-1]
            parent.children.append(node)
        elif tag in self.SELF_CLOSING_TAGS:
            parent = self.unfinished[-1]
            node = Element(tag, attributes, parent)
            parent.children.append(node)
        else:
            parent = self.unfinished[-1] if self.unfinished else None
            node = Element(tag, attributes, parent)
            self.unfinished.append(node)

    def implicit_tags(self, tag):
        while True:
            open_tags = [node.tag for node in self.unfinished]
            if open_tags == [] and tag != "html":
                self.add_tag("html")
            elif open_tags == ["html"] and tag not in ["head", "body", "/html"]:
                if tag in self.HEAD_TAGS:
                    self.add_tag("head")
                else:
                    self.add_tag("body")
            elif open_tags == ["html", "head"] and tag not in ["/head"] + self.HEAD_TAGS:
                self.add_tag("/head")
            else:
                break

    def get_attributes(self, text):
        parts = text.split()
        tag = parts[0].lower()
        attributes = {}
        for pair in parts[1:]:
            if "=" in pair:
                key, value = pair.split("=", 1)
                if len(value) > 2 and value[0] in ("'", '"'):
                    value = value[1:-1]
                attributes[key.lower()] = value
            else:
                attributes[pair.lower()] = ""
        return tag, attributes

    def finish(self):
        if len(self.unfinished) == 0:
            self.add_tag("html")
        while len(self.unfinished) > 1:
            node = self.unfinished.pop()
            parent = self.unfinished[-1]
            parent.children.append(node)
        return self.unfinished.pop()


def print_tree(node, indent=0):
    print(" " * indent, node)
    for child in node.children:
        print_tree(child, indent + 2)


def transform(body):
    body = re.sub("<", "&lt;", body)
    body = re.sub(">", "&gt;", body)
    return "<html><body>" + body + "</html></body>"


FONTS = {}


def get_font(size, weight, slant, family=None):
    key = (size, weight, slant, family)
    if key not in FONTS:
        font = tkinter.font.Font(size=size, weight=weight, slant=slant, family=family)
        FONTS[key] = font
    return FONTS[key]


class Layout:
    def __init__(self, nodes, width=WIDTH, size=16):
        self.display_list = []
        self.cursor_x = HSTEP
        self.cursor_y = HSTEP
        self.weight = "normal"
        self.style = "roman"
        self.width = width
        self.size = size
        self.halign = "normal"
        self.valign = "normal"
        self.abbr = False
        self.pre = False
        self.in_body = False
        self.line = []

        self.recurse(nodes)
        self.flush()

    def open_tag(self, tag):
        if tag == "i":
            self.style = "italic"
        elif tag == "b":
            self.weight = "bold"
        elif tag == "small":
            self.size -= 2
        elif tag == "big":
            self.size += 4
        elif tag == "br":
            self.flush()
        elif tag == "p":
            self.flush()
        elif tag == "h1":
            self.flush()
            self.size += 4
            self.halign = "center"
        elif tag == "sup":
            self.size -= 8
            self.valign = "top"
        elif tag == "abbr":
            self.abbr = True
        elif tag == "pre":
            self.pre = True
        elif tag == "body":
            self.in_body = True

    def close_tag(self, tag):
        if tag == "i":
            self.style = "roman"
        elif tag == "b":
            self.weight = "normal"
        elif tag == "small":
            self.size += 2
        elif tag == "big":
            self.size -= 4
        elif tag == "p":
            self.flush()
            self.cursor_y += VSTEP
        elif tag == "h1":
            self.flush()
            self.size -= 4
            self.halign = "normal"
        elif tag == "sup":
            self.size += 8
            self.valign = "normal"
        elif tag == "abbr":
            self.abbr = False
        elif tag == "pre":
            self.pre = False
        elif tag == "body":
            self.in_body = False

    def recurse(self, tree):
        if isinstance(tree, Text):
            if self.in_body:
                if self.pre:
                    self.pre_text(tree)
                elif self.abbr:
                    self.abbr_text(tree)
                else:
                    self.text(tree)
        else:
            self.open_tag(tree.tag)
            for child in tree.children:
                self.recurse(child)
            self.close_tag(tree.tag)

    def flush_abbr(self, buffer):
        normal_font = get_font(self.size, self.weight, self.style)
        abbr_font = get_font(int(self.size * 0.7), "bold", self.style)

        if buffer.islower():
            buffer = buffer.upper()
            font = abbr_font
        else:
            font = normal_font

        w = font.measure(buffer)
        if self.cursor_x + w > self.width - HSTEP:
            self.flush()

        self.line.append((self.cursor_x, buffer, font, self.valign))
        self.cursor_x += w

    def abbr_text(self, tok):
        font = get_font(self.size, self.weight, self.style)
        for word in tok.text.split():
            buffer = ""
            for c in word:
                if c.islower() == buffer.islower():
                    buffer += c
                else:
                    self.flush_abbr(buffer)
                    buffer = c

            if buffer:
                self.flush_abbr(buffer)

            self.cursor_x += font.measure(" ")

    def pre_text(self, tok):
        font = get_font(self.size, self.weight, self.style, "Courier")
        for line in tok.text.splitlines(keepends=True):
            text = line.rstrip()
            w = font.measure(text)
            self.line.append((self.cursor_x, text, font, "normal"))
            if line.endswith("\n"):
                self.flush()
            else:
                self.cursor_x += w + font.measure(" ")

    def text(self, tok):
        font = get_font(self.size, self.weight, self.style)
        for word in tok.text.split():
            w = font.measure(word)
            if self.cursor_x + w > self.width - HSTEP:
                self.flush()

            self.line.append((self.cursor_x, word, font, self.valign))
            self.cursor_x += w + font.measure(" ")

    def flush(self):
        if not self.line:
            return

        metrics = [font.metrics() for x, word, font, valign in self.line]
        max_ascent = max([metric["ascent"] for metric in metrics])
        baseline = self.cursor_y + 1.25 * max_ascent

        if self.halign == "center":
            x_offset = max((0, (self.width - HSTEP - self.cursor_x) / 2))
        else:
            x_offset = 0

        for x, word, font, valign in self.line:
            if valign == "normal":
                y = baseline - font.metrics('ascent')
            else:
                y = baseline - (max_ascent / 1.25)

            self.display_list.append((x + x_offset, y, word, font))

        self.cursor_x = HSTEP
        self.line = []

        max_descent = max([metric['descent'] for metric in metrics])
        self.cursor_y = baseline + 1.25 * max_descent


class Browser:

    def __init__(self):
        self.window = tkinter.Tk()
        self.window.bind("<Down>", self.scrolldown)
        self.window.bind("<Up>", self.scrollup)
        self.window.bind("<MouseWheel>", self.scroll)
        self.window.bind("<Configure>", self.configure)
        self.window.bind("+", self.fontup)
        self.window.bind("-", self.fontdown)

        self.width = WIDTH
        self.height = HEIGHT

        self.canvas = tkinter.Canvas(self.window, width=self.width, height=self.height)
        self.canvas.pack(expand=True, fill=tkinter.BOTH)
        self.display_list = []
        self.nodes = []
        self.scroll = 0
        self.font_size = FONT_SIZE

    def draw(self):
        self.canvas.delete("all")
        for x, y, c, f in self.display_list:
            if y > self.scroll + self.height:
                continue
            if y + VSTEP < self.scroll:
                continue
            self.canvas.create_text(x, y - self.scroll, text=c, font=f, anchor="nw")

    def layout(self):
        self.display_list = Layout(self.nodes, self.width, self.font_size).display_list

    def configure(self, e):
        self.width = e.width
        self.height = e.height
        self.layout()
        self.draw()

    def scroll(self, e):
        if e.delta < 0:
            self.scrolldown()
        else:
            self.scrollup()

    def scrolldown(self, e=None):
        self.scroll += SCROLL_STEP
        self.draw()

    def scrollup(self, e=None):
        self.scroll -= SCROLL_STEP
        self.scroll = max(self.scroll, 0)
        self.draw()

    def fontup(self, e):
        self.font_size += 2
        self.layout()
        self.draw()

    def fontdown(self, e):
        self.font_size -= 2
        self.layout()
        self.draw()

    def load(self, url):
        if url.startswith("view-source:"):
            url = url[len("view-source:"):]
            headers, body = request(url)
            body = transform(body)
        else:
            headers, body = request(url)

        self.nodes = HTMLParser(body).parse()
        self.layout()
        self.draw()


if __name__ == "__main__":

    Browser().load(DEFAULT_URL)
    tkinter.mainloop()
