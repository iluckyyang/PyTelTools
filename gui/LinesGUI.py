import sys
import logging
import copy
import datetime
import numpy as np

from PyQt5.QtWidgets import *
from PyQt5.QtGui import *
from PyQt5.QtCore import *
import pandas as pd
from itertools import islice, cycle

from slf import Serafin
from geom import Shapefile, BlueKenue
from gui.util import MapViewer, LineMapCanvas, QPlainTextEditLogger, \
    TableWidgetDragRows, OutputProgressDialog, LoadMeshDialog, handleOverwrite, PlotViewer, TimeSlider


class SimpleTimeSelection(QWidget):
    def __init__(self):
        super().__init__()

        self.frames = []
        self.dates = []

        self.index = QLineEdit('', self)
        self.slider = TimeSlider(self.index)
        self.value = QLineEdit('', self)
        self.date = QLineEdit('', self)

        self.value.setMaximumWidth(60)
        self.value.setMaximumWidth(80)
        self.date.setMaximumWidth(120)

        mainLayout = QVBoxLayout()
        mainLayout.addWidget(self.slider)
        hlayout = QHBoxLayout()
        hlayout.addWidget(QLabel('Frame index'))
        hlayout.addWidget(self.index)
        hlayout.addWidget(QLabel('value'))
        hlayout.addWidget(self.value)
        hlayout.addWidget(QLabel('date'))
        hlayout.addWidget(self.date)
        hlayout.addStretch()
        hlayout.setAlignment(Qt.AlignLeft)
        hlayout.setSpacing(10)
        mainLayout.addLayout(hlayout)
        self.setLayout(mainLayout)
        self.index.editingFinished.connect(self.slider.enterIndexEvent)
        self.index.editingFinished.connect(self.updateSelection)

    def updateSelection(self):
        index = int(self.index.text()) - 1
        self.value.setText(str(self.frames[index]))
        self.date.setText(str(self.dates[index]))

    def initTime(self, frames, dates):
        self.frames = frames
        self.dates = dates
        self.slider.reinit(len(frames), 0)
        self.index.setText(str(1))
        self.updateSelection()

    def clearText(self):
        self.index.clear()
        self.value.clear()
        self.date.clear()


class WriteCSVProcess(QThread):
    tick = pyqtSignal(int, name='changed')

    def __init__(self, mesh):
        super().__init__()
        self.mesh = mesh

    def write_header(self, output_stream, selected_vars):
        output_stream.write('Line')
        for header in ['time', 'x', 'y', 'distance'] + selected_vars:
            output_stream.write(';')
            output_stream.write(header)
        output_stream.write('\n')

    def write_csv(self, input_stream, selected_vars, output_stream, line_interpolators,
                        indices_nonemtpy):
        self.write_header(output_stream, selected_vars)

        nb_lines = len(indices_nonemtpy)

        for u, id_line in enumerate(indices_nonemtpy):
            line_interpolator, distances = line_interpolators[id_line]

            for index, time in enumerate(input_stream.time):

                var_values = []
                for var in selected_vars:
                    var_values.append(input_stream.read_var_in_frame(index, var))

                for (x, y, (i, j, k), interpolator), distance in zip(line_interpolator, distances):

                    output_stream.write(str(id_line+1))
                    output_stream.write(';')
                    output_stream.write(str(time))

                    output_stream.write(';')
                    output_stream.write('%.6f' % x)
                    output_stream.write(';')
                    output_stream.write('%.6f' % y)
                    output_stream.write(';')
                    output_stream.write('%.6f' % distance)

                    for i_var, var in enumerate(selected_vars):
                        values = var_values[i_var]
                        output_stream.write(';')
                        output_stream.write('%.6f' % interpolator.dot(values[[i, j, k]]))
                    output_stream.write('\n')

            self.tick.emit(int(100 * (u+1) / nb_lines))
            QApplication.processEvents()


class InputTab(QWidget):
    def __init__(self, parent):
        super().__init__()
        self.parent = parent

        canvas = LineMapCanvas()
        self.map = MapViewer(canvas)
        self.has_map = False

        self.filename = None
        self.header = None
        self.language = 'fr'
        self.time = []
        self.mesh = None

        self.lines = []
        self.line_interpolators = []
        self.line_interpolators_internal = []  # without intersection points

        self._initWidgets()  # some instance attributes will be set there
        self._setLayout()
        self._bindEvents()

    def _initWidgets(self):
        # create a checkbox for language selection
        self.langBox = QGroupBox('Input language')
        hlayout = QHBoxLayout()
        self.frenchButton = QRadioButton('French')
        hlayout.addWidget(self.frenchButton)
        hlayout.addWidget(QRadioButton('English'))
        self.langBox.setLayout(hlayout)
        self.langBox.setMaximumHeight(80)
        self.frenchButton.setChecked(True)

        # create the button open Serafin
        self.btnOpenSerafin = QPushButton('Load\nSerafin', self, icon=self.style().standardIcon(QStyle.SP_DialogOpenButton))
        self.btnOpenSerafin.setToolTip('<b>Open</b> a .slf file')
        self.btnOpenSerafin.setFixedSize(105, 50)

        # create the button open lines
        self.btnOpenLines = QPushButton('Load\nLines', self, icon=self.style().standardIcon(QStyle.SP_DialogOpenButton))
        self.btnOpenLines.setToolTip('<b>Open</b> a .i2s or .shp file')
        self.btnOpenLines.setFixedSize(105, 50)
        self.btnOpenLines.setEnabled(False)

        # create some text fields displaying the IO files info
        self.serafinNameBox = QLineEdit()
        self.serafinNameBox.setReadOnly(True)
        self.serafinNameBox.setFixedHeight(30)
        self.summaryTextBox = QPlainTextEdit()
        self.summaryTextBox.setFixedHeight(50)
        self.summaryTextBox.setReadOnly(True)
        self.linesNameBox = QPlainTextEdit()
        self.linesNameBox.setReadOnly(True)
        self.linesNameBox.setFixedHeight(50)

        # create the map button
        self.btnMap = QPushButton('Locate lines\non map', self, icon=self.style().standardIcon(QStyle.SP_DialogHelpButton))
        self.btnMap.setFixedSize(135, 50)
        self.btnMap.setEnabled(False)

        # create the widget displaying message logs
        self.logTextBox = QPlainTextEditLogger(self)
        self.logTextBox.setFormatter(logging.Formatter('%(asctime)s - [%(levelname)s] - \n%(message)s'))
        logging.getLogger().addHandler(self.logTextBox)
        logging.getLogger().setLevel(logging.INFO)

    def _bindEvents(self):
        self.btnOpenSerafin.clicked.connect(self.btnOpenSerafinEvent)
        self.btnOpenLines.clicked.connect(self.btnOpenLinesEvent)
        self.btnMap.clicked.connect(self.btnMapEvent)

    def _setLayout(self):
        mainLayout = QVBoxLayout()
        mainLayout.addItem(QSpacerItem(10, 10))
        mainLayout.setSpacing(15)
        hlayout = QHBoxLayout()
        hlayout.addItem(QSpacerItem(30, 1))
        hlayout.setAlignment(Qt.AlignLeft)
        hlayout.addWidget(self.btnOpenSerafin)
        hlayout.addItem(QSpacerItem(30, 1))
        hlayout.addWidget(self.langBox)
        hlayout.addItem(QSpacerItem(30, 1))
        hlayout.addWidget(self.btnOpenLines)
        hlayout.addWidget(self.btnMap)
        mainLayout.addLayout(hlayout)
        mainLayout.addItem(QSpacerItem(10, 10))

        glayout = QGridLayout()
        glayout.addWidget(QLabel('     Input file'), 1, 1)
        glayout.addWidget(self.serafinNameBox, 1, 2)
        glayout.addWidget(QLabel('     Summary'), 2, 1)
        glayout.addWidget(self.summaryTextBox, 2, 2)
        glayout.addWidget(QLabel('     Lines file'), 3, 1)
        glayout.addWidget(self.linesNameBox, 3, 2)
        glayout.setAlignment(Qt.AlignLeft)
        glayout.setSpacing(10)
        mainLayout.addLayout(glayout)
        mainLayout.addItem(QSpacerItem(10, 10))

        mainLayout.addWidget(QLabel('   Message logs'))
        mainLayout.addWidget(self.logTextBox.widget)
        self.setLayout(mainLayout)

    def _reinitInput(self, filename):
        self.filename = filename
        self.has_map = False
        self.serafinNameBox.setText(filename)
        self.summaryTextBox.clear()
        self.header = None
        self.time = []
        self.lines = []
        self.line_interpolators = []
        self.line_interpolators_internal = []

        self.btnMap.setEnabled(False)
        self.mesh = None
        self.linesNameBox.clear()
        self.btnOpenLines.setEnabled(True)
        self.parent.reset()

        if not self.frenchButton.isChecked():
            self.language = 'en'
        else:
            self.language = 'fr'

    def btnOpenSerafinEvent(self):
        options = QFileDialog.Options()
        options |= QFileDialog.DontUseNativeDialog
        filename, _ = QFileDialog.getOpenFileName(self, 'Open a .slf file', '',
                                                  'Serafin Files (*.slf);;All Files (*)', QDir.currentPath(), options=options)
        if not filename:
            return

        with Serafin.Read(filename, self.language) as resin:
            resin.read_header()

            # check if the file is 2D
            if not resin.header.is_2d:
                QMessageBox.critical(self, 'Error', 'The file type (TELEMAC 3D) is currently not supported.',
                                     QMessageBox.Ok)
                return

            self._reinitInput(filename)

            # record the time series
            resin.get_time()

            # update the file summary
            self.summaryTextBox.appendPlainText(resin.get_summary())

            # record the mesh for future visualization and calculations
            logging.info('Processing the mesh')
            self.parent.inDialog()
            meshLoader = LoadMeshDialog('interpolation', resin.header)
            self.mesh = meshLoader.run()
            self.parent.outDialog()
            logging.info('Finished processing the mesh')

            # copy to avoid reading the same data in the future
            self.header = copy.deepcopy(resin.header)
            self.time = resin.time[:]

    def btnOpenLinesEvent(self):
        options = QFileDialog.Options()
        options |= QFileDialog.DontUseNativeDialog
        filename, _ = QFileDialog.getOpenFileName(self, 'Open a .i2s or .shp file', '',
                                                  'Line sets (*.i2s);;Shapefile (*.shp);;All Files (*)',
                                                  options=options)
        if not filename:
            return
        is_i2s = filename[-4:] == '.i2s'
        is_shp = filename[-4:] == '.shp'

        if not is_i2s and not is_shp:
            QMessageBox.critical(self, 'Error', 'Only .i2s and .shp file formats are currently supported.',
                                 QMessageBox.Ok)
            return


        self.lines = []
        self.line_interpolators = []
        self.line_interpolators_internal = []

        nb_nonempty = 0

        if is_i2s:
            with BlueKenue.Read(filename) as f:
                f.read_header()
                for poly_name, poly in f.get_open_polylines():
                    line_interpolators, distances, \
                        line_interpolators_internal, distances_internal = self.mesh.get_line_interpolators(poly)
                    if line_interpolators:
                        nb_nonempty += 1
                    self.lines.append(poly)
                    self.line_interpolators.append((line_interpolators, distances))
                    self.line_interpolators_internal.append((line_interpolators_internal, distances_internal))
        else:
            for poly in Shapefile.get_open_polylines(filename):
                line_interpolators, distances, \
                    line_interpolators_internal, distances_internal = self.mesh.get_line_interpolators(poly)
                if line_interpolators:
                    nb_nonempty += 1
                self.lines.append(poly)
                self.line_interpolators.append((line_interpolators, distances))
                self.line_interpolators_internal.append((line_interpolators_internal, distances_internal))
        if not self.lines:
            QMessageBox.critical(self, 'Error', 'The file does not contain any open polyline.',
                                 QMessageBox.Ok)
            return
        if nb_nonempty == 0:
            QMessageBox.critical(self, 'Error', 'No line intersects the mesh continuously.',
                                 QMessageBox.Ok)
            return

        logging.info('Finished reading the lines file %s' % filename)

        self.linesNameBox.clear()
        self.linesNameBox.appendPlainText(filename + '\n' + 'The file contains {} open polyline{}.'
                                          '{} line{} the mesh continuously.'.format(
                                          len(self.lines), 's' if len(self.lines) > 1 else '',
                                          nb_nonempty, 's intersect' if nb_nonempty > 1 else ' intersects'))

        self.has_map = False
        self.btnMap.setEnabled(True)
        self.parent.getInput()

    def btnMapEvent(self):
        if not self.has_map:
            self.map.canvas.reinitFigure(self.mesh, self.lines,
                                         ['Line %d' % (i+1) for i in range(len(self.lines))],
                                         list(islice(cycle(['b', 'r', 'g', 'y', 'k', 'c', '#F28AD6', 'm']),
                                              len(self.lines))))

            self.has_map = True

            self.map.canvas.draw()
            self.has_map = True
        self.map.show()


class CSVTab(QWidget):
    def __init__(self, inputTab, parent):
        super().__init__()
        self.input = inputTab
        self.parent = parent

        self._initWidget()
        self._setLayout()
        self.btnSubmit.clicked.connect(self.btnSubmitEvent)

    def _initWidget(self):
        # create two 3-column tables for variables selection
        self.firstTable = TableWidgetDragRows()
        self.secondTable = TableWidgetDragRows()
        for tw in [self.firstTable, self.secondTable]:
            tw.setColumnCount(3)
            tw.setHorizontalHeaderLabels(['ID', 'Name', 'Unit'])
            vh = tw.verticalHeader()
            vh.setSectionResizeMode(QHeaderView.Fixed)
            vh.setDefaultSectionSize(20)
            hh = tw.horizontalHeader()
            hh.setDefaultSectionSize(110)
            tw.setEditTriggers(QAbstractItemView.NoEditTriggers)
            tw.setMaximumHeight(800)
            tw.setMinimumHeight(250)

        # create the options
        self.intersect = QCheckBox()

        # create the submit button
        self.btnSubmit = QPushButton('Submit\nto .csv', self, icon=self.style().standardIcon(QStyle.SP_DialogSaveButton))
        self.btnSubmit.setToolTip('<b>Write</b> output to .csv')
        self.btnSubmit.setFixedSize(105, 50)
        self.btnSubmit.setEnabled(False)

        # create the output file name box
        self.csvNameBox = QLineEdit()
        self.csvNameBox.setReadOnly(True)
        self.csvNameBox.setFixedHeight(30)

        # create the widget displaying message logs
        self.logTextBox = QPlainTextEditLogger(self)
        self.logTextBox.setFormatter(logging.Formatter('%(asctime)s - [%(levelname)s] - \n%(message)s'))
        logging.getLogger().addHandler(self.logTextBox)
        logging.getLogger().setLevel(logging.INFO)

    def _setLayout(self):
        mainLayout = QVBoxLayout()
        mainLayout.addItem(QSpacerItem(10, 10))
        mainLayout.setSpacing(15)
        glayout = QGridLayout()
        hlayout = QHBoxLayout()
        hlayout.addItem(QSpacerItem(30, 1))
        vlayout = QVBoxLayout()
        lb = QLabel('Available variables')
        vlayout.addWidget(lb)
        vlayout.setAlignment(lb, Qt.AlignHCenter)
        vlayout.addWidget(self.firstTable)
        hlayout2 = QHBoxLayout()
        hlayout2.addItem(QSpacerItem(30, 1))
        hlayout2.addWidget(self.intersect)
        hlayout2.addWidget(QLabel('Add intersection points'))
        hlayout2.setAlignment(self.intersect, Qt.AlignLeft)
        hlayout2.setAlignment(self.intersect, Qt.AlignLeft)
        hlayout2.addStretch()
        vlayout.addLayout(hlayout2)
        hlayout.addLayout(vlayout)
        hlayout.addItem(QSpacerItem(15, 1))

        vlayout = QVBoxLayout()
        lb = QLabel('Output variables')
        vlayout.addWidget(lb)
        vlayout.setAlignment(lb, Qt.AlignHCenter)
        vlayout.addWidget(self.secondTable)
        hlayout.addLayout(vlayout)
        hlayout.addLayout(vlayout)
        hlayout.addItem(QSpacerItem(30, 1))
        glayout.addLayout(hlayout, 1, 1)
        glayout.setAlignment(Qt.AlignLeft)
        glayout.setSpacing(10)
        mainLayout.addLayout(glayout)
        mainLayout.addItem(QSpacerItem(30, 10))

        hlayout = QHBoxLayout()
        hlayout.addItem(QSpacerItem(30, 1))
        hlayout.addWidget(self.btnSubmit)
        hlayout.addWidget(self.csvNameBox)
        mainLayout.addLayout(hlayout)
        mainLayout.addItem(QSpacerItem(30, 15))

        mainLayout.addWidget(QLabel('   Message logs'))
        mainLayout.addWidget(self.logTextBox.widget)
        self.setLayout(mainLayout)

    def getSelectedVariables(self):
        selected = []
        for i in range(self.secondTable.rowCount()):
            selected.append(self.secondTable.item(i, 0).text())
        return selected

    def _initVarTables(self):
        for i, (id, name, unit) in enumerate(zip(self.input.header.var_IDs,
                                                 self.input.header.var_names, self.input.header.var_units)):
            self.firstTable.insertRow(self.firstTable.rowCount())
            id_item = QTableWidgetItem(id.strip())
            name_item = QTableWidgetItem(name.decode('utf-8').strip())
            unit_item = QTableWidgetItem(unit.decode('utf-8').strip())
            self.firstTable.setItem(i, 0, id_item)
            self.firstTable.setItem(i, 1, name_item)
            self.firstTable.setItem(i, 2, unit_item)

    def getInput(self):
        self._initVarTables()
        self.btnSubmit.setEnabled(True)
        self.csvNameBox.clear()

    def reset(self):
        self.firstTable.setRowCount(0)
        self.secondTable.setRowCount(0)
        self.intersect.setChecked(False)
        self.btnSubmit.setEnabled(False)
        self.csvNameBox.clear()

    def btnSubmitEvent(self):
        selected_var_IDs = self.getSelectedVariables()

        if not selected_var_IDs:
            QMessageBox.critical(self, 'Error', 'Choose at least one output variable before submit!',
                                 QMessageBox.Ok)
            return

        options = QFileDialog.Options()
        options |= QFileDialog.DontUseNativeDialog
        options |= QFileDialog.DontConfirmOverwrite
        filename, _ = QFileDialog.getSaveFileName(self, 'Choose the output file name', '',
                                                  'CSV Files (*.csv)', options=options)

        # check the file name consistency
        if not filename:
            return
        if len(filename) < 5 or filename[-4:] != '.csv':
            filename += '.csv'
        overwrite = handleOverwrite(filename)
        if overwrite is None:
            return

        self.csvNameBox.setText(filename)
        logging.info('Writing the output to %s' % filename)
        self.parent.inDialog()

        indices_nonempty = [i for i in range(len(self.input.lines)) if self.input.line_interpolators[i][0]]

        # initialize the progress bar
        process = WriteCSVProcess(self.input.mesh)
        progressBar = OutputProgressDialog()
        progressBar.connectToThread(process)

        with Serafin.Read(self.input.filename, self.input.language) as resin:
            resin.header = self.input.header
            resin.time = self.input.time

            progressBar.setValue(1)
            QApplication.processEvents()

            with open(filename, 'w') as fout:

                if self.intersect.isChecked():
                    process.write_csv(resin, selected_var_IDs, fout,
                                      self.input.line_interpolators, indices_nonempty)
                else:
                    process.write_csv(resin, selected_var_IDs, fout,
                                      self.input.line_interpolators_internal, indices_nonempty)

        logging.info('Finished writing the output')
        progressBar.setValue(100)
        progressBar.cancelButton.setEnabled(True)
        progressBar.exec_()
        self.parent.outDialog()


class MultiVarControlPanel(QWidget):
    def __init__(self):
        super().__init__()
        self.timeSelection = SimpleTimeSelection()
        # create widgets plot options
        self.lineBox = QComboBox()
        self.lineBox.setFixedHeight(30)
        self.lineBox.setMaximumWidth(200)
        self.intersection = QCheckBox()

        self.unitBox = QComboBox()
        self.unitBox.setFixedHeight(30)
        self.unitBox.setMaximumWidth(200)
        self.varList = QListWidget()
        self.varList.setMaximumWidth(200)

        # create the compute button
        self.btnCompute = QPushButton('Compute', icon=self.style().standardIcon(QStyle.SP_DialogApplyButton))
        self.btnCompute.setFixedSize(105, 50)

        # set layout
        vlayout = QVBoxLayout()
        vlayout.addItem(QSpacerItem(10, 10))
        vlayout.addWidget(self.timeSelection)
        vlayout.addItem(QSpacerItem(10, 10))
        hlayout = QHBoxLayout()
        hlayout.addWidget(QLabel('   Select a polyline'))
        hlayout.addWidget(self.lineBox)
        hlayout.setAlignment(self.lineBox, Qt.AlignLeft)
        hlayout.addStretch()
        vlayout.addLayout(hlayout)
        vlayout.addItem(QSpacerItem(10, 10))

        vlayout.addItem(QSpacerItem(10, 10))
        hlayout = QHBoxLayout()
        hlayout.addItem(QSpacerItem(10, 10))
        hlayout.addWidget(self.intersection)
        lb = QLabel('Add intersection points')
        hlayout.addWidget(lb)
        hlayout.setAlignment(lb, Qt.AlignLeft)
        hlayout.addStretch()
        vlayout.addLayout(hlayout)
        vlayout.addItem(QSpacerItem(10, 15))

        hlayout = QHBoxLayout()
        vlayout2 = QVBoxLayout()
        vlayout2.addWidget(self.unitBox)
        vlayout2.addWidget(self.varList)
        vlayout2.setSpacing(10)
        hlayout.addLayout(vlayout2)
        hlayout.addWidget(self.btnCompute)
        hlayout.setAlignment(self.btnCompute, Qt.AlignRight | Qt.AlignTop)
        hlayout.setSpacing(10)
        vlayout.addItem(hlayout)

        self.setLayout(vlayout)


class MultiVariableImageTab(QWidget):
    def __init__(self, inputTab):
        super().__init__()
        self.input = inputTab

        self.var_table = {}
        self.current_vars = []

        # set up a custom plot viewer
        self.plotViewer = PlotViewer()
        self.plotViewer.exitAct.setEnabled(False)
        self.plotViewer.menuBar.setVisible(False)
        self.plotViewer.toolBar.addAction(self.plotViewer.xLabelAct)
        self.plotViewer.toolBar.addSeparator()
        self.plotViewer.toolBar.addAction(self.plotViewer.yLabelAct)
        self.plotViewer.toolBar.addSeparator()
        self.plotViewer.toolBar.addAction(self.plotViewer.titleAct)
        self.plotViewer.canvas.figure.canvas.mpl_connect('motion_notify_event', self.plotViewer.mouseMove)

        # put it in a group box to get a nice border
        self.gb = QGroupBox()
        ly = QHBoxLayout()
        ly.addWidget(self.plotViewer)
        self.gb.setLayout(ly)
        self.gb.setStyleSheet('QGroupBox {border: 8px solid rgb(108, 122, 137); border-radius: 6px }')
        self.gb.setMinimumWidth(600)

        self.control = MultiVarControlPanel()
        self.control.btnCompute.clicked.connect(self.btnComputeEvent)
        self.control.unitBox.currentTextChanged.connect(self._updateList)

        self.splitter = QSplitter()
        self.splitter.addWidget(self.control)
        self.splitter.addWidget(self.gb)

        mainLayout = QHBoxLayout()
        mainLayout.addWidget(self.splitter)
        self.setLayout(mainLayout)

    def _updateList(self, text):
        self.control.varList.clear()
        for var_ID in self.var_table[text.split(': ')[1]]:
            item = QListWidgetItem(var_ID)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)

            item.setCheckState(Qt.Unchecked)
            self.control.varList.addItem(item)

    def _getSelection(self):
        selection = []
        for row in range(self.control.varList.count()):
            item = self.control.varList.item(row)
            if item.checkState() == Qt.Checked:
                selection.append(item.text().split(' (')[0])
        return tuple(selection)

    def btnComputeEvent(self):
        self.current_vars = self._getSelection()
        if not self.current_vars:
            QMessageBox.critical(self, 'Error', 'Select at least one variable to plot.',
                                 QMessageBox.Ok)
            return

        self.plotViewer.current_title = 'Values of variables along line %s' \
                                        % self.control.lineBox.currentText().split()[1]
        line_id = int(self.control.lineBox.currentText().split()[1]) - 1
        if self.control.intersection.isChecked():
            line_interpolator, distances = self.input.line_interpolators[line_id]
        else:
            line_interpolator, distances = self.input.line_interpolators_internal[line_id]
        values = []

        time_index = int(self.timeSelection.index.text()) - 1

        with Serafin.Read(self.input.filename, self.input.language) as input_stream:
            input_stream.header = self.input.header
            input_stream.time = self.input.time
            for var in self.current_vars:
                line_var_values = []
                var_values = input_stream.read_var_in_frame(time_index, var)

                for x, y, (i, j, k), interpolator in line_interpolator:
                    line_var_values.append(interpolator.dot(var_values[[i, j, k]]))
                values.append(line_var_values)

        self.plotViewer.canvas.axes.clear()
        for i, var in enumerate(self.current_vars):
            self.plotViewer.canvas.axes.plot(distances, values[i], '-', linewidth=2, label=var)
        self.plotViewer.canvas.axes.legend()
        self.plotViewer.canvas.axes.grid(linestyle='dotted')
        self.plotViewer.canvas.axes.set_xlabel(self.plotViewer.current_xlabel)
        self.plotViewer.canvas.axes.set_ylabel(self.plotViewer.current_ylabel)
        self.plotViewer.canvas.axes.set_title(self.plotViewer.current_title)
        self.plotViewer.canvas.draw()

    def reset(self):
        self.control.lineBox.clear()
        self.var_table = {}
        self.current_vars = []
        self.control.varList.clear()
        self.plotViewer.defaultPlot()
        self.plotViewer.current_title = ''
        self.plotViewer.current_xlabel = 'Cumulative distance (M)'
        self.plotViewer.current_ylabel = ''
        self.control.timeSelection.clearText()

    def getInput(self):
        if self.input.header.date is not None:
            year, month, day, hour, minute, second = self.input.header.date
            start_time = datetime.datetime(year, month, day, hour, minute, second)
        else:
            start_time = datetime.datetime(1900, 1, 1, 0, 0, 0)
        frames = list(map(lambda x: start_time + datetime.timedelta(seconds=x), self.input.time))
        self.control.timeSelection.initTime(self.input.time, frames)

        for i in range(len(self.input.lines)):
            id_line = str(i+1)
            if self.input.line_interpolators[i][0]:
                self.control.lineBox.addItem('Line %s' % id_line)
        for var_ID, var_name, var_unit in zip(self.input.header.var_IDs, self.input.header.var_names,
                                              self.input.header.var_units):
            var_unit = var_unit.decode('utf-8').strip()
            var_name = var_name.decode('utf-8').strip()
            if not var_unit:
                continue
            if var_unit in self.var_table:
                self.var_table[var_unit].append('%s (%s)' % (var_ID, var_name))
            else:
                self.var_table[var_unit] = ['%s (%s)' % (var_ID, var_name)]

        for var_unit in self.var_table:
            self.control.unitBox.addItem('Unit: %s' % var_unit)
        self._updateList(self.control.unitBox.currentText())


class MultiFrameControlPanel(QWidget):
    def __init__(self):
        super().__init__()
        # create widgets plot options
        self.lineBox = QComboBox()
        self.lineBox.setFixedHeight(30)
        self.lineBox.setMaximumWidth(200)
        self.varBox = QComboBox()
        self.varBox.setMaximumWidth(200)
        self.varBox.setFixedHeight(30)

        self.timeTalbe = QTableWidget()
        self.timeTalbe.setColumnCount(3)
        self.timeTalbe.setHorizontalHeaderLabels(['Index', 'Value', 'Date'])
        vh = self.timeTalbe.verticalHeader()
        vh.setSectionResizeMode(QHeaderView.Fixed)
        vh.setDefaultSectionSize(20)
        hh = self.timeTalbe.horizontalHeader()
        hh.setDefaultSectionSize(110)
        self.timeTalbe.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.timeTalbe.setSelectionMode(QAbstractItemView.NoSelection)
        self.timeTalbe.setMinimumHeight(300)
        self.timeTalbe.setMaximumWidth(400)

        # create the compute button
        self.btnCompute = QPushButton('Compute', icon=self.style().standardIcon(QStyle.SP_DialogApplyButton))
        self.btnCompute.setFixedSize(105, 50)
        self.intersection = QCheckBox()

        vlayout = QVBoxLayout()
        vlayout.addItem(QSpacerItem(10, 10))
        hlayout = QHBoxLayout()
        hlayout.addWidget(QLabel('   Select a polyline'))
        hlayout.addWidget(self.lineBox)
        hlayout.setAlignment(self.lineBox, Qt.AlignLeft)
        hlayout.addStretch()
        vlayout.addLayout(hlayout)
        vlayout.addItem(QSpacerItem(10, 10))
        hlayout = QHBoxLayout()
        hlayout.addWidget(QLabel('   Select a variable'))
        hlayout.addWidget(self.varBox)
        hlayout.setAlignment(self.varBox, Qt.AlignLeft)
        hlayout.addStretch()
        vlayout.addLayout(hlayout)
        vlayout.addItem(QSpacerItem(10, 10))

        hlayout = QHBoxLayout()
        hlayout.addItem(QSpacerItem(10, 10))
        hlayout.addWidget(self.intersection)
        lb = QLabel('Add intersection points')
        hlayout.addWidget(lb)
        hlayout.setAlignment(lb, Qt.AlignLeft)
        hlayout.addStretch()
        vlayout.addLayout(hlayout)
        vlayout.addItem(QSpacerItem(10, 15))

        hlayout = QHBoxLayout()
        hlayout.addWidget(self.timeTalbe)

        hlayout.addWidget(self.btnCompute)
        hlayout.setAlignment(self.btnCompute, Qt.AlignRight | Qt.AlignTop)
        vlayout.addLayout(hlayout)
        self.setLayout(vlayout)


class MultiFrameImageTab(QWidget):
    def __init__(self, inputTab):
        super().__init__()
        self.input = inputTab

        # set up a custom plot viewer
        self.plotViewer = PlotViewer()
        self.plotViewer.exitAct.setEnabled(False)
        self.plotViewer.menuBar.setVisible(False)
        self.plotViewer.toolBar.addAction(self.plotViewer.xLabelAct)
        self.plotViewer.toolBar.addSeparator()
        self.plotViewer.toolBar.addAction(self.plotViewer.yLabelAct)
        self.plotViewer.toolBar.addSeparator()
        self.plotViewer.toolBar.addAction(self.plotViewer.titleAct)
        self.plotViewer.canvas.figure.canvas.mpl_connect('motion_notify_event', self.plotViewer.mouseMove)

        # put it in a group box to get a nice border
        self.gb = QGroupBox()
        ly = QHBoxLayout()
        ly.addWidget(self.plotViewer)
        self.gb.setLayout(ly)
        self.gb.setStyleSheet('QGroupBox {border: 8px solid rgb(108, 122, 137); border-radius: 6px }')
        self.gb.setMinimumWidth(600)

        self.control = MultiFrameControlPanel()
        self.splitter = QSplitter()

        self.splitter.addWidget(self.control)
        self.splitter.addWidget(self.gb)
        mainLayout = QHBoxLayout()

        mainLayout.addWidget(self.splitter)
        self.setLayout(mainLayout)
        self.control.btnCompute.clicked.connect(self.btnComputeEvent)

    def btnComputeEvent(self):
        current_var = self.control.varBox.currentText().split(' (')[0]
        self.plotViewer.current_title = 'Values of %s along line %s' % (current_var,
                                                                        self.control.lineBox.currentText().split()[1])
        line_id = int(self.lineBox.currentText().split()[1]) - 1
        if self.intersection.isChecked():
            line_interpolator, distances = self.input.line_interpolators[line_id]
        else:
            line_interpolator, distances = self.input.line_interpolators_internal[line_id]
        values = []

        time_indices = [0, 1]

        with Serafin.Read(self.input.filename, self.input.language) as input_stream:
            input_stream.header = self.input.header
            input_stream.time = self.input.time
            for index in time_indices:
                line_var_values = []
                var_values = input_stream.read_var_in_frame(index, current_var)

                for x, y, (i, j, k), interpolator in line_interpolator:
                    line_var_values.append(interpolator.dot(var_values[[i, j, k]]))
                values.append(line_var_values)

        self.plotViewer.canvas.axes.clear()
        for i, index in enumerate(time_indices):
            self.plotViewer.canvas.axes.plot(distances, values[i], '-', linewidth=2, label='Frame %d' % (index+1))
        self.plotViewer.canvas.axes.legend()
        self.plotViewer.canvas.axes.grid(linestyle='dotted')
        self.plotViewer.canvas.axes.set_xlabel(self.plotViewer.current_xlabel)
        self.plotViewer.canvas.axes.set_ylabel(self.plotViewer.current_ylabel)
        self.plotViewer.canvas.axes.set_title(self.plotViewer.current_title)
        self.plotViewer.canvas.draw()

    def reset(self):
        self.control.lineBox.clear()
        self.control.varBox.clear()
        self.plotViewer.defaultPlot()
        self.plotViewer.current_title = ''
        self.plotViewer.current_xlabel = 'Cumulative distance (M)'
        self.plotViewer.current_ylabel = ''
        self.control.timeTalbe.setRowCount(0)

    def getInput(self):
        if self.input.header.date is not None:
            year, month, day, hour, minute, second = self.input.header.date
            start_time = datetime.datetime(year, month, day, hour, minute, second)
        else:
            start_time = datetime.datetime(1900, 1, 1, 0, 0, 0)
        frames = list(map(lambda x: start_time + datetime.timedelta(seconds=x), self.input.time))
        for index, value, date in zip(range(len(self.input.time)), self.input.time, frames):
            index_item = QTableWidgetItem(str(1+index))
            value_item = QTableWidgetItem(str(value))
            date_item = QTableWidgetItem(str(date))
            index_item.setCheckState(Qt.Unchecked)
            self.control.timeTalbe.insertRow(index)
            self.control.timeTalbe.setItem(index, 0, index_item)
            self.control.timeTalbe.setItem(index, 1, value_item)
            self.control.timeTalbe.setItem(index, 2, date_item)

        for i in range(len(self.input.lines)):
            id_line = str(i+1)
            if self.input.line_interpolators[i][0]:
                self.control.lineBox.addItem('Line %s' % id_line)
        for var_ID, var_name in zip(self.input.header.var_IDs, self.input.header.var_names):
            var_name = var_name.decode('utf-8').strip()
            self.control.varBox.addItem('%s (%s)' % (var_ID, var_name))


class LinesGUI(QWidget):
    def __init__(self, parent=None):
        super().__init__()
        self.parent = parent

        self.input = InputTab(self)
        self.csvTab = CSVTab(self.input, self)
        self.multiVarTab = MultiVariableImageTab(self.input)
        self.multiFrameTab = MultiFrameImageTab(self.input)

        self.setWindowTitle('Interpolate values of variables along lines')

        self.tab = QTabWidget()
        self.tab.addTab(self.input, 'Input')
        self.tab.addTab(self.csvTab, 'Output to CSV')
        self.tab.addTab(self.multiVarTab, 'Visualization (MultiVar)')
        self.tab.addTab(self.multiFrameTab, 'Visualization (MultiFrame)')

        self.tab.setTabEnabled(1, False)
        self.tab.setTabEnabled(2, False)
        self.tab.setTabEnabled(3, False)

        self.tab.setStyleSheet('QTabBar::tab { height: 40px; width: 150px; }')

        mainLayout = QVBoxLayout()
        mainLayout.addWidget(self.tab)
        self.setLayout(mainLayout)
        self.setWindowFlags(self.windowFlags() | Qt.CustomizeWindowHint)
        self.setMinimumWidth(600)

    def getInput(self):
        self.tab.setTabEnabled(1, True)
        self.tab.setTabEnabled(2, True)
        self.tab.setTabEnabled(3, True)

        self.csvTab.getInput()
        self.multiVarTab.getInput()
        self.multiFrameTab.getInput()

    def reset(self):
        self.tab.setTabEnabled(1, False)
        self.tab.setTabEnabled(2, False)
        self.tab.setTabEnabled(3, False)

        self.csvTab.reset()
        self.multiVarTab.reset()
        self.multiFrameTab.reset()

    def inDialog(self):
        if self.parent is not None:
            self.parent.inDialog()
        else:
            self.setWindowFlags(self.windowFlags() & ~Qt.WindowCloseButtonHint)
            self.setEnabled(False)
            self.show()

    def outDialog(self):
        if self.parent is not None:
            self.parent.outDialog()
        else:
            self.setWindowFlags(self.windowFlags() | Qt.WindowCloseButtonHint)
            self.setEnabled(True)
            self.show()


def exception_hook(exctype, value, traceback):
    """!
    @brief Needed for suppressing traceback silencing in newer version of PyQt5
    """
    sys._excepthook(exctype, value, traceback)
    sys.exit(1)


if __name__ == '__main__':
    # suppress explicitly traceback silencing
    sys._excepthook = sys.excepthook
    sys.excepthook = exception_hook

    app = QApplication(sys.argv)
    widget = LinesGUI()
    widget.show()
    app.exec_()