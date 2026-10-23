# PC-Deck-Gehäuse: Detailbilder Glas/Rahmen (Schnitt durch die Displaymitte, vergrößert, mit Maßen).
# Aufruf: python details.py [Ausgabeordner]
import importlib.util
import os
import shutil
import sys

import cadquery as cq
import vtk
from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = sys.argv[1] if len(sys.argv) > 1 else HERE
sys.argv = [sys.argv[0], os.path.join(HERE, "_tmp_details")]   # gehaeuse.py exportiert sonst in OUT
spec = importlib.util.spec_from_file_location("g", os.path.join(HERE, "gehaeuse.py"))
g = importlib.util.module_from_spec(spec)
os.makedirs(sys.argv[1], exist_ok=True)
try:
    spec.loader.exec_module(g)
except SystemExit:
    pass
shutil.rmtree(os.path.join(HERE, "_tmp_details"), ignore_errors=True)

COLORS = {"kasten": (0.58, 0.61, 0.68), "rahmen": (0.30, 0.32, 0.38), "ts35": (0.20, 0.45, 0.90)}
SOLIDS = {"kasten": g.kasten, "rahmen": g.rahmen, "ts35": g.ts35}
# Schnitt bei X = Displaymitte: alles rechts davon weg
keep = cq.Workplane("XY").box(400, 400, 400).translate((g.lcx - 200, 0, 100))
CUT = {n: w.intersect(keep) for n, w in SOLIDS.items()}


def actors(parts):
    out = []
    for n, w in parts.items():
        poly = w.val().toVtkPolyData(0.01, 0.05)
        nm = vtk.vtkPolyDataNormals()
        nm.SetInputData(poly)
        nm.SetFeatureAngle(30)
        nm.SplittingOn()
        m = vtk.vtkPolyDataMapper()
        m.SetInputConnection(nm.GetOutputPort())
        a = vtk.vtkActor()
        a.SetMapper(m)
        p = a.GetProperty()
        p.SetColor(*COLORS[n])
        p.SetAmbient(0.35)
        p.SetDiffuse(0.85)
        p.SetSpecular(0.2)
        out.append(a)
        fe = vtk.vtkFeatureEdges()
        fe.SetInputData(poly)
        fe.BoundaryEdgesOn()
        fe.FeatureEdgesOn()
        fe.SetFeatureAngle(35)
        fe.ManifoldEdgesOff()
        fe.NonManifoldEdgesOff()
        em = vtk.vtkPolyDataMapper()
        em.SetInputConnection(fe.GetOutputPort())
        em.ScalarVisibilityOff()
        ea = vtk.vtkActor()
        ea.SetMapper(em)
        ea.GetProperty().SetColor(0.05, 0.05, 0.07)
        ea.GetProperty().SetLineWidth(1.5)
        out.append(ea)
    return out


def render(parts, focal, direction, up, half_h, pts, size=(800, 600)):
    """Parallelprojektion um focal; half_h = halbe Bildhöhe in mm. Gibt Bild und Pixel der Punkte zurück."""
    ren = vtk.vtkRenderer()
    ren.SetBackground(1, 1, 1)
    ren.SetBackground2(0.88, 0.91, 0.95)
    ren.GradientBackgroundOn()
    for a in actors(parts):
        ren.AddActor(a)
    win = vtk.vtkRenderWindow()
    win.SetOffScreenRendering(1)
    win.SetSize(*size)
    win.SetMultiSamples(8)
    win.AddRenderer(ren)
    cam = ren.GetActiveCamera()
    f = cq.Vector(*focal)
    d = cq.Vector(*direction).normalized()
    cam.SetFocalPoint(*f.toTuple())
    cam.SetPosition(*(f + d * 300).toTuple())
    cam.SetViewUp(*up)
    cam.ParallelProjectionOn()
    cam.SetParallelScale(half_h)
    ren.ResetCameraClippingRange()
    light = vtk.vtkLight()
    light.SetLightTypeToCameraLight()
    light.SetPosition(0.6, 1.0, 1.2)
    light.SetIntensity(0.45)
    ren.AddLight(light)
    win.Render()
    px = []
    for p in pts:
        ren.SetWorldPoint(*p, 1.0)
        ren.WorldToDisplay()
        x, y, _ = ren.GetDisplayPoint()
        px.append((x, size[1] - y))
    grab = vtk.vtkWindowToImageFilter()
    grab.SetInput(win)
    grab.Update()
    img = grab.GetOutput()
    w, h, _ = img.GetDimensions()
    data = img.GetPointData().GetScalars()
    raw = bytes(data.GetValue(i) for i in range(data.GetNumberOfTuples() * data.GetNumberOfComponents()))
    pil = Image.frombytes("RGB" if data.GetNumberOfComponents() == 3 else "RGBA", (w, h), raw).convert("RGB")
    return pil.transpose(Image.FLIP_TOP_BOTTOM), px


font = ImageFont.truetype("arial.ttf", 20)
fontb = ImageFont.truetype("arialbd.ttf", 22)
RED = (200, 20, 20)


def dim(dr, p0, p1, text, off=(0, 0)):
    """Maßlinie zwischen zwei Bildpunkten mit Text."""
    dr.line([p0, p1], fill=RED, width=2)
    for p in (p0, p1):
        dr.ellipse([p[0] - 4, p[1] - 4, p[0] + 4, p[1] + 4], fill=RED)
    mx, my = (p0[0] + p1[0]) / 2 + off[0], (p0[1] + p1[1]) / 2 + off[1]
    dr.text((mx, my), text, font=fontb, fill=RED, anchor="mm", stroke_width=3, stroke_fill=(255, 255, 255))


def P(S, N, X=None):
    return g.loc(g.lcx if X is None else X, S, N).toTuple()


side = (1, 0, 0)          # Blick von der Schnittfläche (von rechts)
up = g.s_dir.toTuple()    # die Schräge zeigt im Bild nach oben, Front links
mod_s1 = g.lcs + g.LCD_H / 2               # obere Kante Displaymodul (Metallrand)
mod_x1 = g.lcx + g.LCD_W / 2               # rechte Kante Displaymodul (zum Drehknopf)
R = 5.0
panels = []

# 1) obere Displaykante, Schnitt längs der Schräge
pts = [P(g.vs1, 0), P(mod_s1, 0), P(g.vs1 - 1.5, 0), P(g.vs1 - 1.5, g.COVER_T),
       P(mod_s1 + 0.3, g.COVER_T), P(mod_s1 + 0.3, g.N_PCB)]
im, px = render(CUT, P(g.vs1 + 1.5, 2.8), side, up, R, pts)
dr = ImageDraw.Draw(im)
dim(dr, px[0], px[1], f"Lippe {g.BEZEL[2] - g.VIEW_CLR:.1f} über Metallrand", off=(150, 0))
dim(dr, px[2], px[3], f"Stufe {g.COVER_T:.1f}", off=(0, 26))
dim(dr, px[4], px[5], f"Metallrand bis Platine {g.GLASS_T:.1f}", off=(0, -26))
panels.append(("Schnitt obere Displaykante (Front links, Fase 0.5 am Fenster)", im))

# 2) rechte Displaykante (Metallrand 8.2), Schnitt quer bei Displaymitte, Front oben
keep_s = g.dbox(-500, 500, g.lcs, 500, -50, 200)
CUT_S = {n: w.intersect(keep_s) for n, w in SOLIDS.items()}
Q = lambda X, N: g.loc(X, g.lcs, N).toTuple()
pts = [Q(g.vx1, 0), Q(mod_x1, 0), Q(g.vx1 + 0.3, g.COVER_T), Q(g.vx1 + 0.3, g.N_PCB)]
im, px = render(CUT_S, Q(g.vx1 + 4, 2.8), tuple(-g.s_dir), tuple(-g.n_dir), R + 2, pts)
dr = ImageDraw.Draw(im)
dim(dr, (px[0][0], px[0][1] - 40), (px[1][0], px[1][1] - 40), f"Lippe {g.BEZEL[1] - g.VIEW_CLR:.1f}", off=(0, -24))
dim(dr, px[2], px[3], f"{g.GLASS_T:.1f}", off=(30, 0))
panels.append(("Schnitt rechte Displaykante (Metallrand 8.2), Front oben", im))

# 3) Rahmen-Oberkante zum Kasten, Schnitt
top = g.L_SLOPE
pts = [P(top, 0), P(top - g.EDGE_CH, 0), P(top, g.EDGE_CH)]
im, px = render(CUT, P(top - 1.5, 2.5), side, up, R + 1, pts)
dr = ImageDraw.Draw(im)
dim(dr, px[1], px[2], f"Fase {g.EDGE_CH:.1f}", off=(-60, -10))
panels.append(("Schnitt Rahmen-Oberkante / Kasten", im))

# 4) Schnitt durch eine Schraube (unten links): Senkung im Rahmen, Vorbohrung im Kasten
hx, hs = g.pcb2local(*g.TS_HOLES[2])
keep2 = cq.Workplane("XY").box(400, 400, 400).translate((hx - 200, 0, 100))
CUT2 = {n: w.intersect(keep2) for n, w in SOLIDS.items()}
pts = [P(hs, 0, hx), P(hs, g.SCREW_L, hx), P(hs + 3, 0, hx), P(hs + 3, g.SCREW_L + g.PILOT_EXTRA, hx)]
im, px = render(CUT2, P(hs, 7.5, hx), side, up, 9, pts)
dr = ImageDraw.Draw(im)
dim(dr, (px[0][0], px[0][1] + 60), (px[1][0], px[1][1] + 60), f"Schraube {g.SCREW} x {g.SCREW_L:.0f}", off=(0, 24))
dim(dr, (px[2][0], px[2][1] - 60), (px[3][0], px[3][1] - 60), f"Vorbohrung Ø{g.PILOT_D} bis {g.SCREW_L + g.PILOT_EXTRA:.0f}",
    off=(0, -24))
panels.append(("Schnitt Schraube unten links (Holzschraube, Senkung)", im))

W, H = 800, 600
sheet = Image.new("RGB", (2 * W, 2 * (H + 34) + 40), (255, 255, 255))
dr = ImageDraw.Draw(sheet)
dr.text((12, 8), f"Lippe {g.COVER_T} mm deckt den Metallrand ab ({g.BEZEL[0]}/{g.BEZEL[1]}/{g.BEZEL[2]}/{g.BEZEL[3]} mm), "
        f"Fenster = Bildfläche + {g.VIEW_CLR} mm, Fase {g.VIEW_CH} mm, Rahmen {g.LIP} mm, Platine bis Metallrand {g.GLASS_T} mm", font=font, fill=(20, 20, 20))
for k, (name, im) in enumerate(panels):
    x, y = (k % 2) * W, 40 + (k // 2) * (H + 34)
    sheet.paste(im, (x, y))
    dr.text((x + 12, y + H + 4), name, font=font, fill=(20, 20, 20))
sheet.save(os.path.join(OUT, g.PREFIX + "_gehaeuse_details.png"))
print("gespeichert")
