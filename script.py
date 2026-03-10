# -*- coding: utf-8 -*-
"""
SMART GRID ALIGNER (STRICT ORTHOGONAL PRIORITY):
- Priority 1: Vertical/Horizontal Grids (Hard-locked to original axis).
- Priority 2: Diagonal Grids (Vector intersection math).
- Fix: Prevents vertical grids from drifting/locking like diagonal ones.
"""
from pyrevit import revit, DB, forms
import math

# --- CẤU HÌNH ---
doc = revit.doc
uidoc = revit.uidoc

def mm_to_ft(mm_val):
    return mm_val / 304.8

def get_views_smart():
    """Logic chọn View thông minh (Sheet hoặc Active View)"""
    sel_ids = uidoc.Selection.GetElementIds()
    selected_views = []
    
    def is_target_view(v):
        if v.IsTemplate: return False
        valid_types = [
            DB.ViewType.FloorPlan, DB.ViewType.CeilingPlan, 
            DB.ViewType.EngineeringPlan, DB.ViewType.Section, 
            DB.ViewType.Elevation
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

def to_world_cs(point, view_transform):
    return view_transform.OfPoint(point)

def solve_diagonal_intersection(p1, p2, x_min, x_max, y_min, y_max):
    """
    Logic tính toán CHỈ dành cho Grid chéo.
    """
    vec_original = p2 - p1
    candidates = []
    
    dx = vec_original.X
    dy = vec_original.Y
    
    # Tránh chia cho 0
    if abs(dx) < 1e-9: dx = 1e-9
    if abs(dy) < 1e-9: dy = 1e-9

    # Tìm giao điểm với 4 cạnh
    t_xmin = (x_min - p1.X) / dx
    pt_xmin = DB.XYZ(x_min, p1.Y + t_xmin * dy, p1.Z)
    if y_min - 1e-4 <= pt_xmin.Y <= y_max + 1e-4: candidates.append(pt_xmin)

    t_xmax = (x_max - p1.X) / dx
    pt_xmax = DB.XYZ(x_max, p1.Y + t_xmax * dy, p1.Z)
    if y_min - 1e-4 <= pt_xmax.Y <= y_max + 1e-4: candidates.append(pt_xmax)

    t_ymin = (y_min - p1.Y) / dy
    pt_ymin = DB.XYZ(p1.X + t_ymin * dx, y_min, p1.Z)
    if x_min - 1e-4 <= pt_ymin.X <= x_max + 1e-4: candidates.append(pt_ymin)

    t_ymax = (y_max - p1.Y) / dy
    pt_ymax = DB.XYZ(p1.X + t_ymax * dx, y_max, p1.Z)
    if x_min - 1e-4 <= pt_ymax.X <= x_max + 1e-4: candidates.append(pt_ymax)

    # Lọc trùng
    unique_pts = []
    for pt in candidates:
        if not any(pt.DistanceTo(e) < 1e-5 for e in unique_pts):
            unique_pts.append(pt)
            
    if len(unique_pts) < 2: return None, None

    # Tìm cặp xa nhất và đúng chiều
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
                if (pt_b - pt_a).DotProduct(vec_original) > 0:
                    best_start, best_end = pt_a, pt_b
                else:
                    best_start, best_end = pt_b, pt_a
                    
    return best_start, best_end

def main_no_report():
    # 1. Chọn View
    views = get_views_smart()
    if not views:
        forms.alert("Không tìm thấy View hợp lệ.")
        return

    # 2. Nhập Offset
    res = forms.ask_for_string(default="15", prompt="Nhập khoảng cách Offset (mm):", title="Smart Grid Align")
    if not res: return
    try: sheet_offset_mm = float(res)
    except: return

    # 3. Top Zero Option
    has_vertical = any(v.ViewType in [DB.ViewType.Section, DB.ViewType.Elevation] for v in views)
    snap_top_zero = False
    if has_vertical:
        snap_top_zero = forms.alert(
            "Phát hiện MẶT ĐỨNG/CẮT:\nBạn muốn Grid phía TRÊN cắt sát mép Crop (Offset = 0) không?",
            yes=True, no=True
        )

    # 4. Process
    t = DB.Transaction(doc, "Adjust Grids")
    t.Start()
    try:
        # Bật Crop Box trước
        for view in views:
             if not view.CropBoxActive:
                view.CropBoxActive = True
        doc.Regenerate()

        for view in views:
            bbox = view.CropBox
            if not bbox: continue
            
            # --- TÍNH TOÁN KHUNG BAO (BOUNDS) ---
            offset_val = mm_to_ft(sheet_offset_mm * view.Scale)
            b_min, b_max = bbox.Min, bbox.Max
            
            x_min = b_min.X - offset_val
            x_max = b_max.X + offset_val
            y_min = b_min.Y - offset_val
            y_max = b_max.Y + offset_val
            
            # Xử lý riêng cho Section/Elevation nếu chọn Yes
            if snap_top_zero and view.ViewType in [DB.ViewType.Section, DB.ViewType.Elevation]:
                 y_max = b_max.Y

            grids = DB.FilteredElementCollector(doc, view.Id).OfClass(DB.Grid).ToElements()
            v_trans = bbox.Transform

            for grid in grids:
                try:
                    # Reset Scope Box
                    p_scope = grid.LookupParameter("Scope Box")
                    if p_scope and p_scope.AsElementId().IntegerValue != -1:
                          p_scope.Set(DB.ElementId.InvalidElementId)
                    
                    # Chuyển về 2D View Specific
                    grid.SetDatumExtentType(DB.DatumEnds.End0, view, DB.DatumExtentType.ViewSpecific)
                    grid.SetDatumExtentType(DB.DatumEnds.End1, view, DB.DatumExtentType.ViewSpecific)
                    
                    curves = grid.GetCurvesInView(DB.DatumExtentType.ViewSpecific, view)
                    if not curves or not isinstance(curves[0], DB.Line): continue
                    
                    # Lấy tọa độ gốc trong View CS
                    p1 = to_view_cs(curves[0].GetEndPoint(0), v_trans)
                    p2 = to_view_cs(curves[0].GetEndPoint(1), v_trans)
                    
                    dx_raw = abs(p1.X - p2.X)
                    dy_raw = abs(p1.Y - p2.Y)
                    
                    np1, np2 = None, None

                    # --- LOGIC PHÂN TÁCH (QUAN TRỌNG) ---
                    
                    # CASE 1: Grid THẲNG ĐỨNG (Ưu tiên tuyệt đối)
                    if dx_raw < 1e-9: 
                        # GIỮ NGUYÊN tọa độ X gốc (Lấy trung bình để triệt tiêu sai số nhỏ nếu có)
                        fixed_x = (p1.X + p2.X) / 2.0
                        
                        # Chỉ thay đổi Y theo bounds
                        temp_p1 = DB.XYZ(fixed_x, y_min, p1.Z)
                        temp_p2 = DB.XYZ(fixed_x, y_max, p1.Z)
                        
                        # Gán lại đúng chiều đầu/đuôi
                        if p1.Y < p2.Y:
                            np1, np2 = temp_p1, temp_p2
                        else:
                            np1, np2 = temp_p2, temp_p1

                    # CASE 2: Grid NẰM NGANG (Ưu tiên tuyệt đối)
                    elif dy_raw < 1e-9:
                        # GIỮ NGUYÊN tọa độ Y gốc
                        fixed_y = (p1.Y + p2.Y) / 2.0
                        
                        # Chỉ thay đổi X theo bounds
                        temp_p1 = DB.XYZ(x_min, fixed_y, p1.Z)
                        temp_p2 = DB.XYZ(x_max, fixed_y, p1.Z)
                        
                        # Gán lại đúng chiều trái/phải
                        if p1.X < p2.X:
                            np1, np2 = temp_p1, temp_p2
                        else:
                            np1, np2 = temp_p2, temp_p1
                            
                    # CASE 3: Grid CHÉO (Xử lý sau cùng)
                    else:
                        np1, np2 = solve_diagonal_intersection(p1, p2, x_min, x_max, y_min, y_max)

                    # --- ÁP DỤNG ---
                    if np1 and np2:
                        w_p1 = to_world_cs(np1, v_trans)
                        w_p2 = to_world_cs(np2, v_trans)
                        
                        if w_p1.DistanceTo(w_p2) > mm_to_ft(10):
                            # Tạo đường mới
                            nl = DB.Line.CreateBound(w_p1, w_p2)
                            
                            # Kiểm tra lại direction lần cuối để chắc chắn Bubble không bị đảo
                            if not nl.Direction.IsAlmostEqualTo(curves[0].Direction):
                                nl = DB.Line.CreateBound(w_p2, w_p1)
                                
                            grid.SetCurveInView(DB.DatumExtentType.ViewSpecific, view, nl)
                except: pass

        t.Commit()
        uidoc.RefreshActiveView()
    except Exception as e:
        t.RollBack()
        forms.alert("Lỗi: " + str(e))

if __name__ == '__main__':
    main_no_report()