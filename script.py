# -*- coding: utf-8 -*-
"""
SMART GRID ALIGNER (VECTOR SCALING FIX):
- Fixes the "Curve must be on the datum plane" error in sections/elevations.
- Supports Plan, Section, Elevation, and Detail Views.
- Fully English version to prevent UnicodeEncodeError in pyRevit.
"""
from pyrevit import revit, DB, forms

doc = revit.doc
uidoc = revit.uidoc

def mm_to_ft(mm_val):
    return mm_val / 304.8

def get_views_smart():
    sel_ids = uidoc.Selection.GetElementIds()
    selected_views = []
    
    def is_target_view(v):
        if v.IsTemplate: return False
        # Includes Detail View
        valid_types = [
            DB.ViewType.FloorPlan, DB.ViewType.CeilingPlan, 
            DB.ViewType.EngineeringPlan, DB.ViewType.Section, 
            DB.ViewType.Elevation, DB.ViewType.Detail
        ]
        return v.ViewType in valid_types

    if sel_ids:
        for eid in sel_ids:
            el = doc.GetElement(eid)
            if isinstance(el, DB.Viewport):
                view = doc.GetElement(el.ViewId)
                if view and is_target_view(view): selected_views.append(view)
            elif isinstance(el, DB.View) and is_target_view(el):
                selected_views.append(el)

    if not selected_views:
        if is_target_view(doc.ActiveView):
            selected_views.append(doc.ActiveView)

    return selected_views

def to_view_cs(point, view_transform):
    return view_transform.Inverse.OfPoint(point)

def solve_diagonal_intersection(loc_pA, loc_pB, x_min, x_max, y_min, y_max):
    vec_local = loc_pB - loc_pA
    candidates = []
    
    dx = vec_local.X
    dy = vec_local.Y
    
    # Avoid division by zero
    if abs(dx) < 1e-9: dx = 1e-9
    if abs(dy) < 1e-9: dy = 1e-9

    t_xmin = (x_min - loc_pA.X) / dx
    pt_xmin = DB.XYZ(x_min, loc_pA.Y + t_xmin * dy, loc_pA.Z)
    if y_min - 1e-4 <= pt_xmin.Y <= y_max + 1e-4: candidates.append(pt_xmin)

    t_xmax = (x_max - loc_pA.X) / dx
    pt_xmax = DB.XYZ(x_max, loc_pA.Y + t_xmax * dy, loc_pA.Z)
    if y_min - 1e-4 <= pt_xmax.Y <= y_max + 1e-4: candidates.append(pt_xmax)

    t_ymin = (y_min - loc_pA.Y) / dy
    pt_ymin = DB.XYZ(loc_pA.X + t_ymin * dx, y_min, loc_pA.Z)
    if x_min - 1e-4 <= pt_ymin.X <= x_max + 1e-4: candidates.append(pt_ymin)

    t_ymax = (y_max - loc_pA.Y) / dy
    pt_ymax = DB.XYZ(loc_pA.X + t_ymax * dx, y_max, loc_pA.Z)
    if x_min - 1e-4 <= pt_ymax.X <= x_max + 1e-4: candidates.append(pt_ymax)

    unique_pts = []
    for pt in candidates:
        if not any(pt.DistanceTo(e) < 1e-5 for e in unique_pts):
            unique_pts.append(pt)
            
    if len(unique_pts) < 2: return None, None

    best_start = None
    best_end = None
    max_dist = -1

    for i in range(len(unique_pts)):
        for j in range(i + 1, len(unique_pts)):
            pt_a = unique_pts[i]
            pt_b = unique_pts[j]
            dist = pt_a.DistanceTo(pt_b)
            
            if dist > max_dist:
                max_dist = dist
                if (pt_b - pt_a).DotProduct(vec_local) > 0:
                    best_start, best_end = pt_a, pt_b
                else:
                    best_start, best_end = pt_b, pt_a
                    
    return best_start, best_end

def main_no_report():
    views = get_views_smart()
    if not views:
        forms.alert("No valid View found. Please open a Plan, Section, Elevation, or Detail view.")
        return

    res = forms.ask_for_string(default="15", prompt="Enter Offset distance (mm):", title="Smart Grid Align")
    if not res: return
    try: sheet_offset_mm = float(res)
    except: return

    has_vertical = any(v.ViewType in [DB.ViewType.Section, DB.ViewType.Elevation, DB.ViewType.Detail] for v in views)
    snap_top_zero = False
    if has_vertical:
        snap_top_zero = forms.alert(
            "SECTION / ELEVATION / DETAIL detected:\nDo you want the TOP grids to snap exactly to the Crop edge (Offset = 0)?",
            yes=True, no=True
        )

    t = DB.Transaction(doc, "Adjust Grids Extent")
    t.Start()
    try:
        # Enable Crop Box first
        for view in views:
             if not view.CropBoxActive:
                 view.CropBoxActive = True
        doc.Regenerate()

        for view in views:
            bbox = view.CropBox
            if not bbox: continue
            
            offset_val = mm_to_ft(sheet_offset_mm * view.Scale)
            b_min, b_max = bbox.Min, bbox.Max
            
            x_min = b_min.X - offset_val
            x_max = b_max.X + offset_val
            y_min = b_min.Y - offset_val
            y_max = b_max.Y + offset_val
            
            if snap_top_zero and view.ViewType in [DB.ViewType.Section, DB.ViewType.Elevation, DB.ViewType.Detail]:
                 y_max = b_max.Y

            grids = DB.FilteredElementCollector(doc, view.Id).OfClass(DB.Grid).ToElements()
            v_trans = bbox.Transform

            for grid in grids:
                try:
                    p_scope = grid.LookupParameter("Scope Box")
                    if p_scope and p_scope.AsElementId().IntegerValue != -1:
                          p_scope.Set(DB.ElementId.InvalidElementId)
                    
                    grid.SetDatumExtentType(DB.DatumEnds.End0, view, DB.DatumExtentType.ViewSpecific)
                    grid.SetDatumExtentType(DB.DatumEnds.End1, view, DB.DatumExtentType.ViewSpecific)
                    
                    curves = grid.GetCurvesInView(DB.DatumExtentType.ViewSpecific, view)
                    if not curves or not isinstance(curves[0], DB.Line): continue
                    
                    pA = curves[0].GetEndPoint(0)
                    pB = curves[0].GetEndPoint(1)
                    world_vec = pB - pA
                    
                    loc_pA = to_view_cs(pA, v_trans)
                    loc_pB = to_view_cs(pB, v_trans)
                    loc_vec = loc_pB - loc_pA
                    
                    dx_raw = abs(loc_vec.X)
                    dy_raw = abs(loc_vec.Y)
                    
                    t1, t2 = None, None

                    # CASE 1: VERTICAL
                    if dx_raw < 1e-9: 
                        t1 = (y_min - loc_pA.Y) / loc_vec.Y
                        t2 = (y_max - loc_pA.Y) / loc_vec.Y

                    # CASE 2: HORIZONTAL
                    elif dy_raw < 1e-9:
                        t1 = (x_min - loc_pA.X) / loc_vec.X
                        t2 = (x_max - loc_pA.X) / loc_vec.X
                            
                    # CASE 3: DIAGONAL
                    else:
                        np1, np2 = solve_diagonal_intersection(loc_pA, loc_pB, x_min, x_max, y_min, y_max)
                        if np1 and np2:
                            t1 = (np1.X - loc_pA.X) / loc_vec.X
                            t2 = (np2.X - loc_pA.X) / loc_vec.X

                    if t1 is not None and t2 is not None:
                        w_p1 = pA + world_vec * t1
                        w_p2 = pA + world_vec * t2
                        
                        if w_p1.DistanceTo(w_p2) > mm_to_ft(10):
                            nl = DB.Line.CreateBound(w_p1, w_p2)
                            
                            if not nl.Direction.IsAlmostEqualTo(curves[0].Direction):
                                nl = DB.Line.CreateBound(w_p2, w_p1)
                                
                            grid.SetCurveInView(DB.DatumExtentType.ViewSpecific, view, nl)
                except: pass

        t.Commit()
        uidoc.RefreshActiveView()
    except Exception as e:
        t.RollBack()
        forms.alert("Error: " + str(e))

if __name__ == '__main__':
    main_no_report()