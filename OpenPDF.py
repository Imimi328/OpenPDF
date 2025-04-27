#!/usr/bin/env python3
import sys, os, json
from PyQt5 import QtCore, QtGui, QtWidgets
from PyQt5.QtWidgets import QAction, QApplication, QMainWindow, QFileDialog, QColorDialog, QInputDialog, QGraphicsView, QGraphicsScene, QOpenGLWidget, QToolButton, QButtonGroup, QGraphicsPathItem, QGraphicsLineItem, QGraphicsRectItem, QGraphicsEllipseItem, QGraphicsTextItem, QToolBar, QStatusBar, QSlider, QDockWidget, QListWidget, QComboBox, QVBoxLayout, QWidget, QProgressDialog
import fitz  # PyMuPDF

AUTOSAVE_INTERVAL = 60_000  # ms

class AnnotationSaveWorker(QtCore.QThread):
    saved = QtCore.pyqtSignal(str)
    error = QtCore.pyqtSignal(str)
    
    def __init__(self, annotation_path, annotations, parent=None):
        super().__init__(parent)
        self.annotation_path = annotation_path
        self.annotations = annotations

    def run(self):
        try:
            with open(self.annotation_path, 'w') as f:
                json.dump(self.annotations, f)
            self.saved.emit(self.annotation_path)
        except Exception as e:
            self.error.emit(str(e))

class SaveWorker(QtCore.QThread):
    saved = QtCore.pyqtSignal(str)
    error = QtCore.pyqtSignal(str)
    
    def __init__(self, save_path, pdf_path, annotations, render_zoom, parent=None):
        super().__init__(parent)
        self.save_path = save_path
        self.pdf_path = pdf_path
        self.annotations = annotations
        self.render_zoom = render_zoom

    def run(self):
        try:
            doc = fitz.open(self.pdf_path)
            for ann in self.annotations:
                page_idx = ann['page']
                ann_type = ann['type']
                page = doc[page_idx]
                if ann_type == 'path':
                    strokes = ann['strokes']
                    color = ann['color']
                    width = ann['width']
                    annot = page.add_ink_annot(strokes)
                    annot.set_colors(stroke=color)
                    annot.set_border(width=width / self.render_zoom)
                    annot.update()
                elif ann_type == 'line':
                    x1, y1, x2, y2 = ann['points']
                    color = ann['color']
                    width = ann['width']
                    p1 = fitz.Point(x1, y1)
                    p2 = fitz.Point(x2, y2)
                    annot = page.add_line_annot(p1, p2)
                    annot.set_colors(stroke=color)
                    annot.set_border(width=width / self.render_zoom)
                    annot.update()
                elif ann_type in ['rect', 'ellipse']:
                    x, y, w, h = ann['rect']
                    color = ann['color']
                    width = ann['width']
                    rect = fitz.Rect(x, y, x + w, y + h)
                    annot = page.add_rect_annot(rect)
                    annot.set_colors(stroke=color)
                    annot.set_border(width=width / self.render_zoom)
                    annot.update()
                elif ann_type == 'text':
                    x, y, text, font_size = ann['data']
                    color = ann['color']
                    rect = fitz.Rect(x, y, x + 200, y + font_size * 1.5)
                    annot = page.add_freetext_annot(rect, text, fontsize=font_size / self.render_zoom, color=color)
                    annot.update()
                elif ann_type == 'comment':
                    x, y, comment = ann['data']
                    point = fitz.Point(x, y)
                    annot = page.add_text_annot(point, comment)
                    annot.update()
            doc.save(self.save_path, garbage=4, deflate=True)
            doc.close()
            self.saved.emit(self.save_path)
        except Exception as e:
            self.error.emit(str(e))

class ThumbnailWidget(QListWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setViewMode(QListWidget.IconMode)
        self.setIconSize(QtCore.QSize(100, 140))
        self.setResizeMode(QListWidget.Adjust)
        self.setSpacing(10)
        self.setStyleSheet("""
            QListWidget { background: #2a2a2a; border: none; }
            QListWidget::item { padding: 5px; }
            QListWidget::item:selected { background: #3a3a3a; }
        """)

class AnnotatorView(QGraphicsView):
    def __init__(self, scene, parent):
        super().__init__(scene, parent)
        self.parent = parent
        self.setViewport(QOpenGLWidget())
        self.setRenderHints(QtGui.QPainter.Antialiasing | QtGui.QPainter.SmoothPixmapTransform)
        self.setAlignment(QtCore.Qt.AlignTop | QtCore.Qt.AlignLeft)
        self.setViewportUpdateMode(QGraphicsView.FullViewportUpdate)
        self.setDragMode(QGraphicsView.NoDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)

    def mousePressEvent(self, ev):
        if self.parent.current_tool == "pan":
            self.setDragMode(QGraphicsView.ScrollHandDrag)
            super().mousePressEvent(ev)
        elif ev.button() == QtCore.Qt.LeftButton:
            self.parent._start_tool(ev)
        return super().mousePressEvent(ev)

    def mouseMoveEvent(self, ev):
        if self.parent.drawing:
            self.parent._move_tool(ev)
        elif self.parent.current_tool == "pan":
            super().mouseMoveEvent(ev)
        return super().mouseMoveEvent(ev)

    def mouseReleaseEvent(self, ev):
        if self.parent.current_tool == "pan":
            self.setDragMode(QGraphicsView.NoDrag)
        elif ev.button() == QtCore.Qt.LeftButton:
            self.parent._end_tool(ev)
        return super().mouseReleaseEvent(ev)

    def wheelEvent(self, ev):
        if ev.modifiers() & QtCore.Qt.ControlModifier:
            factor = 1.15 if ev.angleDelta().y() > 0 else 1/1.15
            self.parent._zoom(factor)
        else:
            super().wheelEvent(ev)

class PDFAnnotator(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("OpenPDF By Team Emogi")
        self.resize(1400, 900)

        # State
        self.pdf_path = None
        self.doc = None
        self.page_items = []
        self.layers = {"Default": []}
        self.current_layer = "Default"
        self.scene = QGraphicsScene()
        self.view = AnnotatorView(self.scene, self)
        self.render_zoom = 2.0  # Higher quality rendering (144 DPI)
        
        # UI Setup
        self.central_widget = QWidget()
        self.layout = QVBoxLayout(self.central_widget)
        self.setCentralWidget(self.central_widget)
        self.layout.addWidget(self.view)
        
        self.scale = 1.0
        self.current_tool = "pen"
        self.pen_color = QtGui.QColor(255, 255, 0)
        self.pen_width = 4
        self.drawing = False
        self.current_item = None
        self.history = []
        self.redo_stack = []
        self.grid_on = False
        self.grid_item = None
        self.is_fullscreen = False
        self.shortcuts = []

        # Settings for recent files
        self.settings = QtCore.QSettings("MyCompany", "PDFAnnotator")

        # Initialize UI components
        self._apply_modern_theme()
        self._build_toolbar()
        self._build_menu()
        self._setup_dock_widgets()
        self._register_shortcuts()
        self._setup_autosave()
        
        self.status = QStatusBar()
        self.setStatusBar(self.status)
        self._update_status()

    def _apply_modern_theme(self):
        self.setStyleSheet("""
            QMainWindow { background: #252525; }
            QToolBar { 
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1, 
                    stop:0 #3a3a3a, stop:1 #2a2a2a);
                border: none;
                padding: 4px;
            }
            QToolButton { 
                background: #353535;
                border: 1px solid #454545;
                border-radius: 6px;
                padding: 6px;
                margin: 2px;
                color: white;
            }
            QToolButton:checked { 
                background: #505050;
                border: 1px solid #606060;
            }
            QToolButton:hover { 
                background: #454545;
            }
            QMenuBar, QMenu { 
                background: #2a2a2a;
                color: white;
                border: none;
            }
            QMenu::item:selected { 
                background: #3a3a3a;
            }
            QStatusBar { 
                background: #2a2a2a;
                color: white;
                border-top: 1px solid #353535;
            }
            QDockWidget { 
                background: #2a2a2a;
                color: white;
                border: 1px solid #353535;
            }
            QSlider::groove:horizontal {
                background: #353535;
                height: 8px;
                border-radius: 4px;
            }
            QSlider::handle:horizontal {
                background: #606060;
                width: 16px;
                border-radius: 8px;
                margin: -4px 0;
            }
            QComboBox {
                background: #353535;
                color: white;
                border: 1px solid #454545;
                border-radius: 4px;
                padding: 4px;
            }
            QComboBox::drop-down {
                border: none;
                width: 20px;
            }
            QScrollBar:vertical {
                background: #2a2a2a;
                width: 12px;
                margin: 0px;
                border: none;
            }
            QScrollBar::handle:vertical {
                background: #505050;
                min-height: 20px;
                border-radius: 6px;
            }
            QScrollBar::handle:vertical:hover {
                background: #606060;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                background: none;
                height: 0px;
            }
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
                background: none;
            }
            QScrollBar:horizontal {
                background: #2a2a2a;
                height: 12px;
                margin: 0px;
                border: none;
            }
            QScrollBar::handle:horizontal {
                background: #505050;
                min-width: 20px;
                border-radius: 6px;
            }
            QScrollBar::handle:horizontal:hover {
                background: #606060;
            }
            QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
                background: none;
                width: 0px;
            }
            QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {
                background: none;
            }
        """)

    def _build_toolbar(self):
        tb = QToolBar("Tools")
        tb.setIconSize(QtCore.QSize(32, 32))
        tb.setToolButtonStyle(QtCore.Qt.ToolButtonTextUnderIcon)
        self.addToolBar(QtCore.Qt.TopToolBarArea, tb)

        self.btn_group = QButtonGroup(self)
        self.btn_group.setExclusive(True)

        def add_btn(symbol, text, tool=None, checkable=True, slot=None):
            btn = QToolButton()
            btn.setText(f"{symbol}\n{text}")
            btn.setToolTip(text)
            btn.setStatusTip(text)
            btn.setCheckable(checkable)
            if tool:
                btn.clicked.connect(lambda checked, t=tool: self._select_tool(t))
            elif slot:
                btn.clicked.connect(slot)
            tb.addWidget(btn)
            if checkable:
                self.btn_group.addButton(btn)
            return btn

        add_btn("📂", "Open", checkable=False, slot=self.open_pdf)
        add_btn("💾", "Save Annotations", checkable=False, slot=self.save_annotations)
        tb.addSeparator()

        add_btn("🖊️", "Pen", "pen")
        add_btn("🖌️", "Highlighter", "high")
        add_btn("➖", "Line", "line")
        add_btn("➤", "Arrow", "arrow")
        add_btn("▭", "Rectangle", "rect")
        add_btn("◯", "Ellipse", "ellipse")
        add_btn("🅰️", "Text", "text")
        add_btn("💬", "Comment", "comment")
        add_btn("🧽", "Eraser", "eraser")
        add_btn("✋", "Pan", "pan")
        tb.addSeparator()

        self.swatch = QtWidgets.QLabel()
        self.swatch.setFixedSize(32, 32)
        self._update_swatch()
        add_btn("🎨", "Color", checkable=False, slot=self._choose_color)
        tb.addWidget(self.swatch)

        self.width_slider = QSlider(QtCore.Qt.Horizontal)
        self.width_slider.setRange(1, 50)
        self.width_slider.setValue(self.pen_width)
        self.width_slider.setFixedWidth(100)
        self.width_slider.valueChanged.connect(self._update_pen_width)
        tb.addWidget(self.width_slider)

        tb.addSeparator()
        add_btn("🔍+", "Zoom In", checkable=False, slot=lambda: self._zoom(1.15))
        add_btn("🔍-", "Zoom Out", checkable=False, slot=lambda: self._zoom(1/1.15))
        add_btn("↶", "Undo", checkable=False, slot=self.undo)
        add_btn("↷", "Redo", checkable=False, slot=self.redo)

        self._select_tool("pen")

    def _setup_dock_widgets(self):
        self.thumbnail_dock = QDockWidget("Pages", self)
        self.thumbnail_list = ThumbnailWidget()
        self.thumbnail_dock.setWidget(self.thumbnail_list)
        self.addDockWidget(QtCore.Qt.LeftDockWidgetArea, self.thumbnail_dock)
        self.thumbnail_list.itemClicked.connect(self._thumbnail_clicked)

        self.layer_dock = QDockWidget("Layers", self)
        layer_widget = QWidget()
        layer_layout = QVBoxLayout(layer_widget)
        
        self.layer_combo = QComboBox()
        self.layer_combo.addItem("Default")
        self.layer_combo.currentTextChanged.connect(self._change_layer)
        layer_layout.addWidget(self.layer_combo)
        
        add_layer_btn = QToolButton()
        add_layer_btn.setText("➕ Add Layer")
        add_layer_btn.clicked.connect(self._add_layer)
        layer_layout.addWidget(add_layer_btn)
        
        self.layer_dock.setWidget(layer_widget)
        self.addDockWidget(QtCore.Qt.RightDockWidgetArea, self.layer_dock)

    def _build_menu(self):
        mb = self.menuBar()
        def mitem(menu, text, slot):
            a = QAction(text, self)
            a.triggered.connect(slot)
            menu.addAction(a)

        file_menu = mb.addMenu("&File")
        mitem(file_menu, "Open...", self.open_pdf)
        self.recent_menu = file_menu.addMenu("Recent Files")
        self._update_recent_menu()
        mitem(file_menu, "Save Annotations", self.save_annotations)
        mitem(file_menu, "Export Annotated PDF...", self.export_pdf)
        file_menu.addSeparator()
        mitem(file_menu, "Exit", self.close)

        edit_menu = mb.addMenu("&Edit")
        mitem(edit_menu, "Undo", self.undo)
        mitem(edit_menu, "Redo", self.redo)
        mitem(edit_menu, "Clear All", self.clear_all)

        view_menu = mb.addMenu("&View")
        mitem(view_menu, "Zoom In", lambda: self._zoom(1.15))
        mitem(view_menu, "Zoom Out", lambda: self._zoom(1/1.15))
        mitem(view_menu, "Fit Width", self.fit_width)
        mitem(view_menu, "Fit Height", self.fit_height)
        mitem(view_menu, "Toggle Fullscreen", self._toggle_fullscreen)
        mitem(view_menu, "Toggle Grid", self.toggle_grid)
        mitem(view_menu, "Toggle Thumbnails", lambda: self.thumbnail_dock.setVisible(not self.thumbnail_dock.isVisible()))
        mitem(view_menu, "Toggle Layers", lambda: self.layer_dock.setVisible(not self.layer_dock.isVisible()))

        help_menu = mb.addMenu("&Help")
        mitem(help_menu, "About", self.show_about)

    def _update_recent_menu(self):
        self.recent_menu.clear()
        recent_files = self.settings.value("recent_files", [])
        for file in recent_files:
            a = QAction(file, self)
            a.triggered.connect(lambda checked, f=file: self._open_recent(f))
            self.recent_menu.addAction(a)

    def _open_recent(self, path):
        if os.path.exists(path):
            self._load_pdf(path)
        else:
            QtWidgets.QMessageBox.warning(self, "File Not Found", f"The file {path} does not exist.")
            recent_files = self.settings.value("recent_files", [])
            if path in recent_files:
                recent_files.remove(path)
                self.settings.setValue("recent_files", recent_files)
                self._update_recent_menu()

    def _register_shortcuts(self):
        for shortcut in getattr(self, 'shortcuts', []):
            shortcut.deleteLater()
        self.shortcuts = []

        shortcuts = [
            ("Ctrl+O", self.open_pdf),
            ("Ctrl+S", self.save_annotations),
            ("Ctrl+Z", self.undo),
            ("Ctrl+Y", self.redo),
            ("Ctrl++", lambda: self._zoom(1.15)),
            ("Ctrl+-", lambda: self._zoom(1/1.15)),
            ("Ctrl+W", self.fit_width),
            ("Ctrl+H", self.fit_height),
            ("Ctrl+G", self.toggle_grid),
            ("Ctrl+T", lambda: self.thumbnail_dock.setVisible(not self.thumbnail_dock.isVisible())),
            ("Ctrl+L", lambda: self.layer_dock.setVisible(not self.layer_dock.isVisible())),
            ("PageUp", self.page_up),
            ("PageDown", self.page_down),
            ("F11", self._toggle_fullscreen),
            ("Esc", lambda: self._toggle_fullscreen() if self.is_fullscreen else None)
        ]
        for key, func in shortcuts:
            shortcut = QtWidgets.QShortcut(QtGui.QKeySequence(key), self)
            shortcut.activated.connect(func)
            shortcut.setContext(QtCore.Qt.ApplicationShortcut)
            self.shortcuts.append(shortcut)

    def _setup_autosave(self):
        self.autosave_timer = QtCore.QTimer(self)
        self.autosave_timer.timeout.connect(self.save_annotations)
        self.autosave_timer.start(AUTOSAVE_INTERVAL)

    def open_pdf(self):
        path, _ = QFileDialog.getOpenFileName(self, "Open PDF", "", "PDF Files (*.pdf)")
        if not path:
            return
        self._load_pdf(path)

    def _load_pdf(self, path):
        self.pdf_path = path
        try:
            self.doc = fitz.open(path)
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Error", f"Failed to open PDF: {str(e)}")
            return

        self.scene.clear()
        self.page_items.clear()
        self.thumbnail_list.clear()
        self.layers = {"Default": []}
        self.layer_combo.clear()
        self.layer_combo.addItem("Default")

        progress = QProgressDialog("Loading PDF pages...", "Cancel", 0, self.doc.page_count, self)
        progress.setWindowModality(QtCore.Qt.NonModal)
        progress.setMinimumDuration(0)

        y = 0
        for i in range(self.doc.page_count):
            if progress.wasCanceled():
                self.doc.close()
                self.doc = None
                self._update_status()
                return
            
            progress.setValue(i)
            QApplication.processEvents()
            
            try:
                pg = self.doc.load_page(i)
                m = fitz.Matrix(self.render_zoom, self.render_zoom)  # 144 DPI
                pm = pg.get_pixmap(matrix=m, alpha=False)
                img = QtGui.QImage(pm.samples, pm.width, pm.height, pm.stride, QtGui.QImage.Format_RGB888)
                img.invertPixels()  # Dark mode
                pix = QtGui.QPixmap.fromImage(img)
                item = QtWidgets.QGraphicsPixmapItem(pix)
                item.setPos(0, y)
                self.scene.addItem(item)
                self.page_items.append(item)
                
                thumb = pix.scaled(100, 140, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)
                thumb_item = QtWidgets.QListWidgetItem(QtGui.QIcon(thumb), f"Page {i+1}")
                self.thumbnail_list.addItem(thumb_item)
                
                y += pix.height() + 20
                del pm, img, pix, thumb
            except Exception as e:
                print(f"Failed to load page {i}: {str(e)}")
                continue

        progress.setValue(self.doc.page_count)
        
        self.view.setSceneRect(self.scene.itemsBoundingRect())
        self.history.clear()
        self.redo_stack.clear()
        self.view.verticalScrollBar().setValue(0)
        self._update_status()

        self._load_annotations()

        recent_files = self.settings.value("recent_files", [])
        if self.pdf_path in recent_files:
            recent_files.remove(self.pdf_path)
        recent_files.insert(0, self.pdf_path)
        if len(recent_files) > 10:
            recent_files = recent_files[:10]
        self.settings.setValue("recent_files", recent_files)
        self._update_recent_menu()

    def save_annotations(self):
        if not self.doc:
            return
        annotations = self.collect_annotations()
        annotation_path = os.path.splitext(self.pdf_path)[0] + '.annotations.json'
        self._annotation_save_worker = AnnotationSaveWorker(annotation_path, annotations, self)
        self._annotation_save_worker.saved.connect(lambda path: self.status.showMessage(f"Annotations saved to {path}", 3000))
        self._annotation_save_worker.error.connect(lambda err: QtWidgets.QMessageBox.critical(self, "Save Error", f"Failed to save annotations: {err}"))
        self._annotation_save_worker.start()

    def export_pdf(self):
        if not self.doc:
            return
        dest, _ = QFileDialog.getSaveFileName(self, "Export Annotated PDF", "", "PDF Files (*.pdf)")
        if not dest:
            return
        annotations = self.collect_annotations()
        self._save_worker = SaveWorker(dest, self.pdf_path, annotations, self.render_zoom, self)
        self._save_worker.saved.connect(lambda path: self.status.showMessage(f"Exported to {path}", 3000))
        self._save_worker.error.connect(lambda err: QtWidgets.QMessageBox.critical(self, "Export Error", f"Failed to export: {err}"))
        self._save_worker.start()

    def collect_annotations(self):
        annotations = []
        for layer_name, layer_items in self.layers.items():
            for item, page_idx in layer_items:
                if isinstance(item, QGraphicsPathItem):
                    path = item.path()
                    strokes = []
                    current_stroke = []
                    for i in range(path.elementCount()):
                        elem = path.elementAt(i)
                        if elem.isMoveTo():
                            if current_stroke:
                                strokes.append(current_stroke)
                            current_stroke = [[elem.x / self.render_zoom, elem.y / self.render_zoom]]
                        elif elem.isLineTo():
                            current_stroke.append([elem.x / self.render_zoom, elem.y / self.render_zoom])
                    if current_stroke:
                        strokes.append(current_stroke)
                    color = list(item.pen().color().getRgbF()[:3])
                    width = item.pen().widthF()
                    annotations.append({
                        'layer': layer_name,
                        'page': page_idx,
                        'type': 'path',
                        'strokes': strokes,
                        'color': color,
                        'width': width
                    })
                elif isinstance(item, QGraphicsLineItem):
                    line = item.line()
                    color = list(item.pen().color().getRgbF()[:3])
                    width = item.pen().widthF()
                    annotations.append({
                        'layer': layer_name,
                        'page': page_idx,
                        'type': 'line',
                        'points': [line.x1() / self.render_zoom, line.y1() / self.render_zoom, line.x2() / self.render_zoom, line.y2() / self.render_zoom],
                        'color': color,
                        'width': width
                    })
                elif isinstance(item, QGraphicsRectItem):
                    rect = item.rect()
                    color = list(item.pen().color().getRgbF()[:3])
                    width = item.pen().widthF()
                    annotations.append({
                        'layer': layer_name,
                        'page': page_idx,
                        'type': 'rect',
                        'rect': [rect.x() / self.render_zoom, rect.y() / self.render_zoom, rect.width() / self.render_zoom, rect.height() / self.render_zoom],
                        'color': color,
                        'width': width
                    })
                elif isinstance(item, QGraphicsEllipseItem) and not item.toolTip():
                    rect = item.rect()
                    color = list(item.pen().color().getRgbF()[:3])
                    width = item.pen().widthF()
                    annotations.append({
                        'layer': layer_name,
                        'page': page_idx,
                        'type': 'ellipse',
                        'rect': [rect.x() / self.render_zoom, rect.y() / self.render_zoom, rect.width() / self.render_zoom, rect.height() / self.render_zoom],
                        'color': color,
                        'width': width
                    })
                elif isinstance(item, QGraphicsTextItem):
                    pos = item.pos()
                    text = item.toPlainText()
                    font_size = item.font().pointSizeF()
                    color = list(item.defaultTextColor().getRgbF()[:3])
                    annotations.append({
                        'layer': layer_name,
                        'page': page_idx,
                        'type': 'text',
                        'data': [pos.x() / self.render_zoom, pos.y() / self.render_zoom, text, font_size],
                        'color': color
                    })
                elif isinstance(item, QGraphicsEllipseItem) and item.toolTip():
                    pos = item.pos()
                    comment = item.toolTip()
                    annotations.append({
                        'layer': layer_name,
                        'page': page_idx,
                        'type': 'comment',
                        'data': [pos.x() / self.render_zoom, pos.y() / self.render_zoom, comment]
                    })
        return annotations

    def _load_annotations(self):
        annotation_path = os.path.splitext(self.pdf_path)[0] + '.annotations.json'
        if not os.path.exists(annotation_path):
            return
        try:
            with open(annotation_path, 'r') as f:
                annotations = json.load(f)
            for ann in annotations:
                layer_name = ann['layer']
                if layer_name not in self.layers:
                    self.layers[layer_name] = []
                    self.layer_combo.addItem(layer_name)
                page_idx = ann['page']
                ann_type = ann['type']
                if ann_type == 'path':
                    strokes = [[[p[0] * self.render_zoom, p[1] * self.render_zoom] for p in stroke] for stroke in ann['strokes']]
                    color = QtGui.QColor.fromRgbF(*ann['color'], 1.0)
                    width = ann['width']
                    pen = QtGui.QPen(color, width, QtCore.Qt.SolidLine, QtCore.Qt.RoundCap, QtCore.Qt.RoundJoin)
                    item = QGraphicsPathItem()
                    item.setPen(pen)
                    path = QtGui.QPainterPath()
                    for stroke in strokes:
                        path.moveTo(*stroke[0])
                        for point in stroke[1:]:
                            path.lineTo(*point)
                    item.setPath(path)
                    item.setPos(self.page_items[page_idx].pos())
                    self.scene.addItem(item)
                    self.layers[layer_name].append((item, page_idx))
                elif ann_type == 'line':
                    x1, y1, x2, y2 = [p * self.render_zoom for p in ann['points']]
                    color = QtGui.QColor.fromRgbF(*ann['color'], 1.0)
                    width = ann['width']
                    pen = QtGui.QPen(color, width, QtCore.Qt.SolidLine, QtCore.Qt.RoundCap, QtCore.Qt.RoundJoin)
                    item = QGraphicsLineItem(x1, y1, x2, y2)
                    item.setPen(pen)
                    item.setPos(self.page_items[page_idx].pos())
                    self.scene.addItem(item)
                    self.layers[layer_name].append((item, page_idx))
                elif ann_type == 'rect':
                    x, y, w, h = [p * self.render_zoom for p in ann['rect']]
                    color = QtGui.QColor.fromRgbF(*ann['color'], 1.0)
                    width = ann['width']
                    pen = QtGui.QPen(color, width, QtCore.Qt.SolidLine)
                    item = QGraphicsRectItem(x, y, w, h)
                    item.setPen(pen)
                    item.setBrush(QtGui.QBrush(QtCore.Qt.NoBrush))
                    item.setPos(self.page_items[page_idx].pos())
                    self.scene.addItem(item)
                    self.layers[layer_name].append((item, page_idx))
                elif ann_type == 'ellipse':
                    x, y, w, h = [p * self.render_zoom for p in ann['rect']]
                    color = QtGui.QColor.fromRgbF(*ann['color'], 1.0)
                    width = ann['width']
                    pen = QtGui.QPen(color, width, QtCore.Qt.SolidLine)
                    item = QGraphicsEllipseItem(x, y, w, h)
                    item.setPen(pen)
                    item.setBrush(QtGui.QBrush(QtCore.Qt.NoBrush))
                    item.setPos(self.page_items[page_idx].pos())
                    self.scene.addItem(item)
                    self.layers[layer_name].append((item, page_idx))
                elif ann_type == 'text':
                    x, y, text, font_size = ann['data']
                    x, y, font_size = x * self.render_zoom, y * self.render_zoom, font_size
                    color = QtGui.QColor.fromRgbF(*ann['color'], 1.0)
                    item = QGraphicsTextItem(text)
                    item.setDefaultTextColor(color)
                    item.setFont(QtGui.QFont("Arial", font_size))
                    item.setPos(x, y)
                    item.setPos(self.page_items[page_idx].pos() + QtCore.QPointF(x, y))
                    item.setFlag(QtWidgets.QGraphicsItem.ItemIsMovable)
                    item.setFlag(QtWidgets.QGraphicsItem.ItemIsSelectable)
                    self.scene.addItem(item)
                    self.layers[layer_name].append((item, page_idx))
                elif ann_type == 'comment':
                    x, y, comment = ann['data']
                    x, y = x * self.render_zoom, y * self.render_zoom
                    item = QGraphicsEllipseItem(-8, -8, 16, 16)
                    item.setBrush(QtGui.QColor(255, 255, 0))
                    item.setPen(QtGui.QPen(QtGui.QColor(0, 0, 0), 1))
                    item.setToolTip(comment)
                    item.setFlag(QtWidgets.QGraphicsItem.ItemIsMovable)
                    item.setPos(self.page_items[page_idx].pos() + QtCore.QPointF(x, y))
                    self.scene.addItem(item)
                    self.layers[layer_name].append((item, page_idx))
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "Load Error", f"Failed to load annotations: {str(e)}")

    def _start_tool(self, ev):
        pos = self.view.mapToScene(ev.pos())
        page_idx = self._get_page_at(pos)
        if page_idx is None:
            return
        page_pos = self.page_items[page_idx].pos()
        local_pos = pos - page_pos
        color = QtGui.QColor(self.pen_color)
        if self.current_tool == "high":
            color.setAlpha(120)
        pen = QtGui.QPen(color, self.pen_width, QtCore.Qt.SolidLine, QtCore.Qt.RoundCap, QtCore.Qt.RoundJoin)
        
        if self.current_tool in ["pen", "high"]:
            item = QGraphicsPathItem()
            item.setPen(pen)
            path = QtGui.QPainterPath()
            path.moveTo(local_pos)
            item.setPath(path)
            item.setPos(page_pos)
            self.scene.addItem(item)
            self.current_item = item
            self.drawing = True
            self.layers[self.current_layer].append((item, page_idx))
            self.history.append(("add", item, page_idx, self.current_layer))
            self.redo_stack.clear()
        elif self.current_tool in ["line", "arrow"]:
            item = QGraphicsLineItem(local_pos.x(), local_pos.y(), local_pos.x(), local_pos.y())
            item.setPen(pen)
            item.setPos(page_pos)
            self.scene.addItem(item)
            self.current_item = item
            self.drawing = True
            self.layers[self.current_layer].append((item, page_idx))
            self.history.append(("add", item, page_idx, self.current_layer))
            self.redo_stack.clear()
        elif self.current_tool == "rect":
            item = QGraphicsRectItem(local_pos.x(), local_pos.y(), 0, 0)
            item.setPen(pen)
            item.setBrush(QtGui.QBrush(QtCore.Qt.NoBrush))
            item.setPos(page_pos)
            self.scene.addItem(item)
            self.current_item = item
            self.drawing = True
            self.layers[self.current_layer].append((item, page_idx))
            self.history.append(("add", item, page_idx, self.current_layer))
            self.redo_stack.clear()
        elif self.current_tool == "ellipse":
            item = QGraphicsEllipseItem(local_pos.x(), local_pos.y(), 0, 0)
            item.setPen(pen)
            item.setBrush(QtGui.QBrush(QtCore.Qt.NoBrush))
            item.setPos(page_pos)
            self.scene.addItem(item)
            self.current_item = item
            self.drawing = True
            self.layers[self.current_layer].append((item, page_idx))
            self.history.append(("add", item, page_idx, self.current_layer))
            self.redo_stack.clear()
        elif self.current_tool == "text":
            text, ok = QInputDialog.getText(self, "Text", "Enter text:")
            if ok and text:
                item = QGraphicsTextItem(text)
                item.setDefaultTextColor(color)
                item.setFont(QtGui.QFont("Arial", max(8, self.pen_width * 2)))
                item.setPos(page_pos + local_pos)
                item.setFlag(QtWidgets.QGraphicsItem.ItemIsMovable)
                item.setFlag(QtWidgets.QGraphicsItem.ItemIsSelectable)
                self.scene.addItem(item)
                self.layers[self.current_layer].append((item, page_idx))
                self.history.append(("add", item, page_idx, self.current_layer))
                self.redo_stack.clear()
        elif self.current_tool == "comment":
            comment, ok = QInputDialog.getMultiLineText(self, "Comment", "Enter comment:")
            if ok and comment:
                item = QGraphicsEllipseItem(-8, -8, 16, 16)
                item.setBrush(QtGui.QColor(255, 255, 0))
                item.setPen(QtGui.QPen(QtGui.QColor(0, 0, 0), 1))
                item.setToolTip(comment)
                item.setFlag(QtWidgets.QGraphicsItem.ItemIsMovable)
                item.setPos(page_pos + local_pos)
                self.scene.addItem(item)
                self.layers[self.current_layer].append((item, page_idx))
                self.history.append(("add", item, page_idx, self.current_layer))
                self.redo_stack.clear()
        elif self.current_tool == "eraser":
            self.drawing = True
            self.current_item = None

    def _move_tool(self, ev):
        if not self.drawing:
            return
        pos = self.view.mapToScene(ev.pos())
        page_idx = self._get_page_at(pos)
        if page_idx is None:
            return
        page_pos = self.page_items[page_idx].pos()
        local_pos = pos - page_pos
        if self.current_tool in ["pen", "high"] and self.current_item:
            path = self.current_item.path()
            path.lineTo(local_pos)
            self.current_item.setPath(path)
        elif self.current_tool in ["line", "arrow"] and self.current_item:
            line = self.current_item.line()
            self.current_item.setLine(line.x1(), line.y1(), local_pos.x(), local_pos.y())
        elif self.current_tool in ["rect", "ellipse"] and self.current_item:
            rect = QtCore.QRectF(QtCore.QPointF(self.current_item.rect().x(), self.current_item.rect().y()), local_pos).normalized()
            self.current_item.setRect(rect)
        elif self.current_tool == "eraser":
            rect = QtCore.QRectF(pos - QtCore.QPointF(10, 10), QtCore.QSizeF(20, 20))
            items = self.scene.items(rect)
            for item in items:
                if isinstance(item, (QGraphicsPathItem, QGraphicsLineItem, QGraphicsRectItem, QGraphicsEllipseItem, QGraphicsTextItem)):
                    for layer_name, layer_items in self.layers.items():
                        for i, (it, pg) in enumerate(layer_items):
                            if it == item and pg == page_idx:
                                del layer_items[i]
                                self.scene.removeItem(item)
                                self.history.append(("remove", item, page_idx, layer_name))
                                self.redo_stack.clear()
                                break

    def _end_tool(self, ev):
        self.drawing = False
        self.current_item = None

    def _get_page_at(self, pos):
        for i, item in enumerate(self.page_items):
            rect = item.sceneBoundingRect()
            if rect.contains(pos):
                return i
        return None

    def _select_tool(self, tool):
        self.current_tool = tool
        for btn in self.btn_group.buttons():
            if btn.toolTip() == tool.capitalize():
                btn.setChecked(True)

    def _choose_color(self):
        color = QColorDialog.getColor(self.pen_color, self)
        if color.isValid():
            self.pen_color = color
            self._update_swatch()

    def _update_swatch(self):
        pix = QtGui.QPixmap(32, 32)
        pix.fill(self.pen_color)
        self.swatch.setPixmap(pix)

    def _update_pen_width(self, value):
        self.pen_width = value

    def _zoom(self, factor):
        self.scale *= factor
        self.view.scale(factor, factor)
        self._update_status()

    def undo(self):
        if not self.history:
            return
        action, item, page_idx, layer = self.history.pop()
        if action == "add":
            self.scene.removeItem(item)
            self.layers[layer].remove((item, page_idx))
            self.redo_stack.append(("add", item, page_idx, layer))
        elif action == "remove":
            self.scene.addItem(item)
            self.layers[layer].append((item, page_idx))
            self.redo_stack.append(("remove", item, page_idx, layer))

    def redo(self):
        if not self.redo_stack:
            return
        action, item, page_idx, layer = self.redo_stack.pop()
        if action == "add":
            self.scene.addItem(item)
            self.layers[layer].append((item, page_idx))
            self.history.append(("add", item, page_idx, layer))
        elif action == "remove":
            self.scene.removeItem(item)
            self.layers[layer].remove((item, page_idx))
            self.history.append(("remove", item, page_idx, layer))

    def clear_all(self):
        self.scene.clear()
        self.page_items.clear()
        self.thumbnail_list.clear()
        self.layers = {"Default": []}
        self.layer_combo.clear()
        self.layer_combo.addItem("Default")
        self.history.clear()
        self.redo_stack.clear()
        self._update_status()

    def fit_width(self):
        if self.page_items:
            rect = self.page_items[0].pixmap().rect()
            view_width = self.view.viewport().width()
            self.scale = view_width / (rect.width() * self.render_zoom)
            self.view.resetTransform()
            self.view.scale(self.scale, self.scale)
            self._update_status()

    def fit_height(self):
        if self.page_items:
            rect = self.scene.itemsBoundingRect()
            view_height = self.view.viewport().height()
            self.scale = view_height / rect.height()
            self.view.resetTransform()
            self.view.scale(self.scale, self.scale)
            self._update_status()

    def toggle_grid(self):
        self.grid_on = not self.grid_on
        if self.grid_on and not self.grid_item:
            rect = self.scene.itemsBoundingRect()
            self.grid_item = QtWidgets.QGraphicsRectItem(rect)
            pen = QtGui.QPen(QtGui.QColor(100, 100, 100, 50), 1, QtCore.Qt.DotLine)
            self.grid_item.setPen(pen)
            for x in range(0, int(rect.width()), 50):
                line = QGraphicsLineItem(x, 0, x, rect.height())
                line.setPen(pen)
                self.grid_item.addToGroup(line)
            for y in range(0, int(rect.height()), 50):
                line = QGraphicsLineItem(0, y, rect.width(), y)
                line.setPen(pen)
                self.grid_item.addToGroup(line)
            self.scene.addItem(self.grid_item)
        elif not self.grid_on and self.grid_item:
            self.scene.removeItem(self.grid_item)
            self.grid_item = None

    def _toggle_fullscreen(self):
        self.is_fullscreen = not self.is_fullscreen
        if self.is_fullscreen:
            self.showFullScreen()
        else:
            self.showNormal()

    def page_up(self):
        value = self.view.verticalScrollBar().value()
        self.view.verticalScrollBar().setValue(value - self.view.viewport().height())

    def page_down(self):
        value = self.view.verticalScrollBar().value()
        self.view.verticalScrollBar().setValue(value + self.view.viewport().height())

    def _thumbnail_clicked(self, item):
        idx = self.thumbnail_list.row(item)
        if idx < len(self.page_items):
            pos = self.page_items[idx].pos()
            self.view.centerOn(pos.x(), pos.y())

    def _change_layer(self, layer_name):
        self.current_layer = layer_name

    def _add_layer(self):
        name, ok = QInputDialog.getText(self, "New Layer", "Layer name:")
        if ok and name and name not in self.layers:
            self.layers[name] = []
            self.layer_combo.addItem(name)
            self.layer_combo.setCurrentText(name)

    def _update_status(self):
        status = f"Scale: {self.scale:.2%}"
        if self.pdf_path:
            status += f" | File: {os.path.basename(self.pdf_path)} | Pages: {self.doc.page_count if self.doc else 0}"
        self.status.showMessage(status)

    def show_about(self):
        QtWidgets.QMessageBox.about(self, "About", "Enhanced PDF Annotator\nVersion 1.0")

if __name__ == "__main__":
    app = QApplication(sys.argv)
    win = PDFAnnotator()
    win.show()
    sys.exit(app.exec_())