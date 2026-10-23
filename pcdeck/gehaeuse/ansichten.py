# PC-Deck-Gehäuse: Ansichtsbilder mit VTK (unsichtbar gerendert, mit Licht und Kanten).
# Aufruf: python ansichten.py [Ausgabeordner]
import importlib.util
import os
import sys

import vtk
from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = sys.argv[1] if len(sys.argv) > 1 else HERE
spec = importlib.util.spec_from_file_location("g", os.path.join(HERE, "gehaeuse.py"))
g = importlib.util.module_from_spec(spec)
try:
    spec.loader.exec_module(g)
except SystemExit:
    pass

# Teil, Farbe, Deckkraft
PARTS = {
    "kasten": (g.kasten, (0.58, 0.61, 0.68), 1.0),
    "rahmen": (g.rahmen, (0.30, 0.32, 0.38), 1.0),
    "ts35": (g.ts35, (0.20, 0.45, 0.90), 1.0),
    "esp": (g.esp_attrappe, (0.15, 0.62, 0.32), 1.0),
    "ec11": (g.enc, (0.65, 0.65, 0.68), 1.0),
}


def actors(names):
    out = []
    for n in names:
        w, col, alpha = PARTS[n]
        poly = w.val().toVtkPolyData(0.05, 0.1)
        normals = vtk.vtkPolyDataNormals()
        normals.SetInputData(poly)
        normals.SetFeatureAngle(30)
        normals.SplittingOn()
        m = vtk.vtkPolyDataMapper()
        m.SetInputConnection(normals.GetOutputPort())
        a = vtk.vtkActor()
        a.SetMapper(m)
        p = a.GetProperty()
        p.SetColor(*col)
        p.SetOpacity(alpha)
        p.SetAmbient(0.35)
        p.SetDiffuse(0.85)
        p.SetSpecular(0.25)
        p.SetSpecularPower(20)
        out.append(a)
        # Kanten (Ecken der Form) als feine dunkle Linien
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
        ea.GetProperty().SetLineWidth(1.2)
        out.append(ea)
    return out


def render(names, direction, up=(0, 0, 1), size=(800, 560)):
    ren = vtk.vtkRenderer()
    ren.SetBackground(1, 1, 1)
    ren.SetBackground2(0.86, 0.89, 0.94)
    ren.GradientBackgroundOn()
    for a in actors(names):
        ren.AddActor(a)
    win = vtk.vtkRenderWindow()
    win.SetOffScreenRendering(1)
    win.SetSize(*size)
    win.SetMultiSamples(8)
    win.AddRenderer(ren)
    cam = ren.GetActiveCamera()
    cam.SetFocalPoint(0, 0, 0)
    cam.SetPosition(*direction)
    cam.SetViewUp(*up)
    cam.ParallelProjectionOn()
    ren.ResetCamera()
    cam.Zoom(1.15)
    ren.ResetCameraClippingRange()
    light = vtk.vtkLight()
    light.SetLightTypeToCameraLight()
    light.SetPosition(0.6, 1.0, 1.2)
    light.SetIntensity(0.45)
    ren.AddLight(light)
    win.Render()
    grab = vtk.vtkWindowToImageFilter()
    grab.SetInput(win)
    grab.Update()
    img = grab.GetOutput()
    w, h, _ = img.GetDimensions()
    data = img.GetPointData().GetScalars()
    raw = bytes(data.GetValue(i) for i in range(data.GetNumberOfTuples() * data.GetNumberOfComponents()))
    pil = Image.frombytes("RGB" if data.GetNumberOfComponents() == 3 else "RGBA", (w, h), raw).convert("RGB")
    return pil.transpose(Image.FLIP_TOP_BOTTOM)


ALL = ["kasten", "rahmen", "ts35", "esp", "ec11"]
views = [
    ("vorne links", ALL, (-1.0, -1.6, 1.0)),
    ("vorne, senkrecht aufs Display", ["kasten", "rahmen", "ts35"], tuple(-g.n_dir)),
    ("Rahmen von hinten (Stege, Lasche)", ["rahmen"], (0.35, 0.766, -0.643)),
    ("hinten rechts (USB)", ["kasten"], (1.2, 1.4, 0.9)),
    ("innen, ohne Rahmen", ["kasten", "ts35", "esp", "ec11"], (-0.8, -1.4, 1.6)),
    ("Kasten innen", ["kasten"], (0.6, -1.2, 1.8)),
]
W, H = 800, 560
sheet = Image.new("RGB", (3 * W, 2 * (H + 34)), (255, 255, 255))
font = ImageFont.truetype("arial.ttf", 22)
for k, (name, names, direction) in enumerate(views):
    im = render(names, direction, size=(W, H))
    x, y = (k % 3) * W, (k // 3) * (H + 34)
    sheet.paste(im, (x, y))
    ImageDraw.Draw(sheet).text((x + 12, y + H + 4), name, font=font, fill=(20, 20, 20))
sheet.save(os.path.join(OUT, g.PREFIX + "_gehaeuse_ansichten.png"))
print("gespeichert")
