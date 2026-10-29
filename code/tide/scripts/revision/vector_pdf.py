"""Small dependency-free vector PDF renderer for audited experiment plots."""
from __future__ import annotations

import math
from pathlib import Path

COLORS = {"tide": (.08, .43, .63), "bare": (.77, .45, .12),
          "ray": (.57, .32, .64), "kubeedge": (.17, .55, .38)}
LABELS = {"tide": "Tide", "bare": "Bare", "ray": "Ray Serve", "kubeedge": "KubeEdge EdgeCore"}
SYSTEMS = ("tide", "bare", "ray", "kubeedge")

# Small vector glyphs keep plot labels visible even where PDF viewers have no
# substitute for an unembedded core font. The text operators remain for copy.
GLYPHS = {
    "A": ("01110","10001","10001","11111","10001","10001","10001"),
    "B": ("11110","10001","10001","11110","10001","10001","11110"),
    "C": ("01111","10000","10000","10000","10000","10000","01111"),
    "D": ("11110","10001","10001","10001","10001","10001","11110"),
    "E": ("11111","10000","10000","11110","10000","10000","11111"),
    "F": ("11111","10000","10000","11110","10000","10000","10000"),
    "G": ("01111","10000","10000","10111","10001","10001","01111"),
    "H": ("10001","10001","10001","11111","10001","10001","10001"),
    "I": ("11111","00100","00100","00100","00100","00100","11111"),
    "J": ("00111","00010","00010","00010","10010","10010","01100"),
    "K": ("10001","10010","10100","11000","10100","10010","10001"),
    "L": ("10000","10000","10000","10000","10000","10000","11111"),
    "M": ("10001","11011","10101","10101","10001","10001","10001"),
    "N": ("10001","11001","10101","10011","10001","10001","10001"),
    "O": ("01110","10001","10001","10001","10001","10001","01110"),
    "P": ("11110","10001","10001","11110","10000","10000","10000"),
    "Q": ("01110","10001","10001","10001","10101","10010","01101"),
    "R": ("11110","10001","10001","11110","10100","10010","10001"),
    "S": ("01111","10000","10000","01110","00001","00001","11110"),
    "T": ("11111","00100","00100","00100","00100","00100","00100"),
    "U": ("10001","10001","10001","10001","10001","10001","01110"),
    "V": ("10001","10001","10001","10001","10001","01010","00100"),
    "W": ("10001","10001","10001","10101","10101","10101","01010"),
    "X": ("10001","10001","01010","00100","01010","10001","10001"),
    "Y": ("10001","10001","01010","00100","00100","00100","00100"),
    "Z": ("11111","00001","00010","00100","01000","10000","11111"),
    "0": ("01110","10001","10011","10101","11001","10001","01110"),
    "1": ("00100","01100","00100","00100","00100","00100","01110"),
    "2": ("01110","10001","00001","00010","00100","01000","11111"),
    "3": ("11110","00001","00001","01110","00001","00001","11110"),
    "4": ("00010","00110","01010","10010","11111","00010","00010"),
    "5": ("11111","10000","10000","11110","00001","00001","11110"),
    "6": ("01110","10000","10000","11110","10001","10001","01110"),
    "7": ("11111","00001","00010","00100","01000","01000","01000"),
    "8": ("01110","10001","10001","01110","10001","10001","01110"),
    "9": ("01110","10001","10001","01111","00001","00001","01110"),
    "-": ("00000","00000","00000","11111","00000","00000","00000"),
    "/": ("00001","00001","00010","00100","01000","10000","10000"),
    ".": ("00000","00000","00000","00000","00000","01100","01100"),
    ",": ("00000","00000","00000","00000","00110","00100","01000"),
    ":": ("00000","01100","01100","00000","01100","01100","00000"),
    ";": ("00000","01100","01100","00000","00110","00100","01000"),
    "%": ("11001","11010","00100","01000","10110","00110","00000"),
    "(": ("00010","00100","01000","01000","01000","00100","00010"),
    ")": ("01000","00100","00010","00010","00010","00100","01000"),
    " ": ("00000",)*7,
}


def _escape(value):
    return str(value).replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


class Canvas:
    def __init__(self, width, height):
        self.width, self.height = width, height
        self.ops = []

    def line(self, x1, y1, x2, y2, color=(0, 0, 0), width=1):
        r, g, b = color
        self.ops.append(f"{r:.3f} {g:.3f} {b:.3f} RG {width:.2f} w "
                        f"{x1:.2f} {y1:.2f} m {x2:.2f} {y2:.2f} l S")

    def circle(self, x, y, radius=2.5, color=(0, 0, 0)):
        r, g, b = color
        k = .5522847498 * radius
        self.ops.append(f"{r:.3f} {g:.3f} {b:.3f} RG {r:.3f} {g:.3f} {b:.3f} rg "
                        f"{x+radius:.2f} {y:.2f} m "
                        f"{x+radius:.2f} {y+k:.2f} {x+k:.2f} {y+radius:.2f} {x:.2f} {y+radius:.2f} c "
                        f"{x-k:.2f} {y+radius:.2f} {x-radius:.2f} {y+k:.2f} {x-radius:.2f} {y:.2f} c "
                        f"{x-radius:.2f} {y-k:.2f} {x-k:.2f} {y-radius:.2f} {x:.2f} {y-radius:.2f} c "
                        f"{x+k:.2f} {y-radius:.2f} {x+radius:.2f} {y-k:.2f} {x+radius:.2f} {y:.2f} c B")

    def text(self, x, y, value, size=9, color=(0, 0, 0), center=False):
        value = str(value)
        scale = size / 7
        if center:
            x -= len(value) * 6 * scale / 2
        r, g, b = color
        self.ops.append(f"{r:.3f} {g:.3f} {b:.3f} rg BT /F1 {size:.2f} Tf "
                        f"1 0 0 1 {x:.2f} {y:.2f} Tm ({_escape(value)}) Tj ET")
        for index, character in enumerate(value.upper()):
            glyph = GLYPHS.get(character, GLYPHS[" "])
            for row, pattern in enumerate(glyph):
                for col, pixel in enumerate(pattern):
                    if pixel == "1":
                        px = x + (index*6 + col)*scale
                        py = y + (6-row)*scale
                        self.ops.append(f"{r:.3f} {g:.3f} {b:.3f} rg "
                                        f"{px:.2f} {py:.2f} {scale:.2f} {scale:.2f} re f")

    def save(self, path):
        stream = ("\n".join(self.ops) + "\n").encode("ascii")
        objects = [
            b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            (f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {self.width} {self.height}] "
             "/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>").encode(),
            f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"endstream",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        ]
        data = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        offsets = [0]
        for index, obj in enumerate(objects, 1):
            offsets.append(len(data))
            data.extend(f"{index} 0 obj\n".encode() + obj + b"\nendobj\n")
        xref = len(data)
        data.extend(f"xref\n0 {len(objects)+1}\n0000000000 65535 f \n".encode())
        for offset in offsets[1:]:
            data.extend(f"{offset:010d} 00000 n \n".encode())
        data.extend((f"trailer\n<< /Size {len(objects)+1} /Root 1 0 R >>\n"
                     f"startxref\n{xref}\n%%EOF\n").encode())
        Path(path).write_bytes(data)


def plot_grid(path, panels, columns, footer):
    """Panel: title, rates, series: {system: [{mean,ci95_low,ci95_high}]}.

    Confidence intervals are drawn at their actual values, including bounds
    outside the physical range; zero is included on each vertical axis.
    """
    rows = math.ceil(len(panels) / columns)
    width = 990 if columns == 3 else 780
    panel_width = 290 if columns == 3 else 340
    gap = 30
    panel_height = 202
    height = 70 + rows * panel_height + 30
    canvas = Canvas(width, height)
    canvas.text(width / 2, height-28, "Independent runs; 95% Student-t intervals", 11, center=True)
    legend_x = 92 if columns == 3 else 70
    for system in SYSTEMS:
        color = COLORS[system]
        canvas.line(legend_x, height-48, legend_x+18, height-48, color, 2)
        canvas.circle(legend_x+9, height-48, 2.4, color)
        canvas.text(legend_x+24, height-51, LABELS[system], 9)
        legend_x += 165
    for index, panel in enumerate(panels):
        row, col = divmod(index, columns)
        left = 53 + col * (panel_width + gap)
        bottom = height - 82 - (row+1) * panel_height + 48
        plot_width, plot_height = panel_width-42, 132
        rates = panel["rates"]
        estimates = [point for system in SYSTEMS for point in panel["series"][system]]
        y_low = min(0, *(point["ci95_low"] for point in estimates))
        y_high = max(0, *(point["ci95_high"] for point in estimates))
        if y_high <= y_low:
            y_high = y_low+1
        pad = (y_high-y_low) * .06
        y_low -= pad
        y_high += pad
        canvas.text(left + plot_width/2, bottom+plot_height+19, panel["title"], 10, center=True)
        for fraction in (0, .5, 1):
            value = y_low + fraction*(y_high-y_low)
            y = bottom + fraction*plot_height
            canvas.line(left, y, left+plot_width, y, (.84,.84,.84), .5)
            canvas.text(left-38, y-3, f"{value:.1f}", 7)
        canvas.line(left, bottom, left, bottom+plot_height, (.2,.2,.2), .9)
        canvas.line(left, bottom, left+plot_width, bottom, (.2,.2,.2), .9)
        xmap = lambda rate: left + (rate-min(rates))/(max(rates)-min(rates))*plot_width
        ymap = lambda value: bottom + (value-y_low)/(y_high-y_low)*plot_height
        for rate in rates:
            x = xmap(rate)
            canvas.line(x, bottom, x, bottom-3, (.2,.2,.2), .7)
            canvas.text(x, bottom-14, rate, 8, center=True)
        for system in SYSTEMS:
            points = panel["series"][system]
            color = COLORS[system]
            positions = [(xmap(rate), ymap(point["mean"])) for rate, point in zip(rates, points)]
            for (x1,y1),(x2,y2) in zip(positions, positions[1:]):
                canvas.line(x1,y1,x2,y2,color,1.5)
            for rate, point, (x,y) in zip(rates, points, positions):
                low, high = ymap(point["ci95_low"]), ymap(point["ci95_high"])
                canvas.line(x, low, x, high, color, .9)
                canvas.line(x-3, low, x+3, low, color, .9)
                canvas.line(x-3, high, x+3, high, color, .9)
                canvas.circle(x,y,2.2,color)
        canvas.text(left + plot_width/2, bottom-29, "Offered rate (req/s)", 8, center=True)
    canvas.text(width/2, 11, footer, 8, center=True)
    canvas.save(path)
