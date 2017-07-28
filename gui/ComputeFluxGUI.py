import sys
import logging
import datetime
import numpy as np
import pandas as pd
import struct

from PyQt5.QtWidgets import *
from PyQt5.QtGui import *
from PyQt5.QtCore import *

from slf import Serafin
from slf.flux import FluxCalculator
from geom import BlueKenue, Shapefile
from gui.util import TemporalPlotViewer, QPlainTextEditLogger, LineMapCanvas, MapViewer, OutputThread, \
    OutputProgressDialog, LoadMeshDialog, SerafinInputTab, TelToolWidget, handleOverwrite, testOpen


class FluxCalculatorThread(OutputThread):
    def __init__(self, flux_type, var_IDs, input_stream, section_names, sections,
                 time_sampling_frequency, mesh, separator):
        super().__init__()

        self.calculator = FluxCalculator(flux_type, var_IDs, input_stream,
                                         section_names, sections, time_sampling_frequency)
        self.mesh = mesh
        self.separator = separator

    def run_calculator(self):
        self.tick.emit(6)
        QApplication.processEvents()

        logging.info('Starting to process the mesh')
        self.calculator.mesh = self.mesh
        self.tick.emit(15)
        QApplication.processEvents()

        self.calculator.construct_intersections()
        self.tick.emit(30)
        QApplication.processEvents()
        logging.info('Finished processing the mesh')

        result = []

        for i, time_index in enumerate(self.calculator.time_indices):
            if self.canceled:
                return []

            i_result = [str(self.calculator.input_stream.time[time_index])]
            values = []
            for var_ID in self.calculator.var_IDs:
                values.append(self.calculator.input_stream.read_var_in_frame(time_index, var_ID))

            for j in range(len(self.calculator.sections)):
                intersections = self.calculator.intersections[j]
                flux = self.calculator.flux_in_frame(intersections, values)
                i_result.append('%.6f' % flux)
            result.append(i_result)
            self.tick.emit(30 + int(70 * (i+1) / len(self.calculator.time_indices)))
            QApplication.processEvents()

        return result

    def write_csv(self, output_stream):
        result = self.run_calculator()
        self.calculator.write_csv(result, output_stream, self.separator)


class InputTab(SerafinInputTab):
    def __init__(self, parent):
        super().__init__(parent)
        self.old_options = ('1', '')

        self.filename = None
        self.header = None
        self.mesh = None
        self.language = 'fr'
        self.time = []
        self.polylines = []
        self.var_IDs = []

        self._initWidgets()  # some instance attributes will be set there
        self._setLayout()
        self._bindEvents()

        self.setMinimumWidth(800)

    def _initWidgets(self):
        # create the button open Polygon
        self.btnOpenPolyline = QPushButton('Load\nSections', self, icon=self.style().standardIcon(QStyle.SP_DialogOpenButton))
        self.btnOpenPolyline.setToolTip('<b>Open</b> a .i2s or .shp file')
        self.btnOpenPolyline.setFixedSize(105, 50)
        self.btnOpenPolyline.setEnabled(False)

        # create some text fields displaying the IO files info
        self.polygonNameBox = QPlainTextEdit()
        self.polygonNameBox.setReadOnly(True)
        self.polygonNameBox.setFixedHeight(50)
        self.csvNameBox = QLineEdit()
        self.csvNameBox.setReadOnly(True)
        self.csvNameBox.setFixedHeight(30)

        # create some widgets for flux options
        self.fluxBox = QComboBox()
        self.fluxBox.setFixedSize(400, 30)
        self.timeSampling = QLineEdit('1')
        self.timeSampling.setFixedWidth(50)

        # create the submit button
        self.btnSubmit = QPushButton('Submit\n(to .csv)', self, icon=self.style().standardIcon(QStyle.SP_DialogSaveButton))
        self.btnSubmit.setFixedSize(105, 50)
        self.btnSubmit.setEnabled(False)

    def _bindEvents(self):
        self.btnOpen.clicked.connect(self.btnOpenSerafinEvent)
        self.btnOpenPolyline.clicked.connect(self.btnOpenPolylineEvent)
        self.btnSubmit.clicked.connect(self.btnSubmitEvent)
        self.timeSampling.editingFinished.connect(self._checkSamplingFrequency)

    def _setLayout(self):
        mainLayout = QVBoxLayout()
        mainLayout.addItem(QSpacerItem(10, 10))
        mainLayout.setSpacing(15)
        mainLayout.addLayout(self.input_layout)
        mainLayout.addItem(QSpacerItem(10, 20))

        hlayout = QHBoxLayout()
        hlayout.addItem(QSpacerItem(30, 1))
        hlayout.addWidget(self.btnOpenPolyline)
        hlayout.addWidget(self.polygonNameBox)
        mainLayout.addLayout(hlayout)
        mainLayout.addItem(QSpacerItem(10, 10))

        glayout = QGridLayout()
        glayout.addWidget(QLabel('     Select the flux to compute'), 1, 1)
        glayout.addWidget(self.fluxBox, 1, 2)
        hlayout = QHBoxLayout()
        hlayout.addWidget(QLabel('Time sampling frequency'))
        hlayout.addWidget(self.timeSampling)
        hlayout.setAlignment(self.timeSampling, Qt.AlignLeft)
        hlayout.addStretch()
        glayout.addLayout(hlayout, 2, 2)

        glayout.setAlignment(Qt.AlignLeft)
        glayout.setSpacing(10)
        mainLayout.addLayout(glayout)

        hlayout = QHBoxLayout()
        hlayout.addItem(QSpacerItem(30, 1))
        hlayout.addWidget(self.btnSubmit)
        hlayout.addWidget(self.csvNameBox)
        mainLayout.addLayout(hlayout)

        mainLayout.addItem(QSpacerItem(10, 15))
        mainLayout.addWidget(QLabel('   Message logs'))
        mainLayout.addWidget(self.logTextBox.widget)
        self.setLayout(mainLayout)

    def _reinitInput(self, filename):
        self.filename = filename
        self.inNameBox.setText(filename)
        self.summaryTextBox.clear()
        self.csvNameBox.clear()
        self.header = None
        self.time = []
        self.mesh = None
        self.csvNameBox.clear()
        self.old_options = (self.timeSampling.text(),
                            self.fluxBox.currentText())
        self.btnOpenPolyline.setEnabled(False)
        self.timeSampling.setText('1')
        self.fluxBox.clear()
        self.btnSubmit.setEnabled(False)

        if not self.frenchButton.isChecked():
            self.language = 'en'
        else:
            self.language = 'fr'

    def _resetDefaultOptions(self):
        sampling_frequency, flux_type = self.old_options
        if int(sampling_frequency) <= len(self.time):
            self.timeSampling.setText(sampling_frequency)
        for i in range(self.fluxBox.count()):
            text = self.fluxBox.itemText(i)
            if text == flux_type:
                self.fluxBox.setCurrentIndex(i)
                self.btnSubmit.setEnabled(True)
                break

    def _addFluxOptions(self, header):
        if 'U' in header.var_IDs and 'V' in header.var_IDs:
            if 'H' in header.var_IDs:
                self.fluxBox.addItem('Liquid flux (m3/s): (U, V, H)')
                for name in header.var_names:
                    str_name = name.decode('utf-8').strip()
                    if 'TRACEUR' in str_name or 'TRACER' in str_name:
                        self.fluxBox.addItem('Solid flux (kg/s): (U, V, H, %s)' % str_name)
        if 'I' in header.var_IDs and 'J' in header.var_IDs:
            self.fluxBox.addItem('Liquid flux (m3/s): (I, J)')
        if 'H' in header.var_IDs and 'M' in header.var_IDs:
            self.fluxBox.addItem('Liquid flux (m3/s): (M, H)')
        if 'Q' in header.var_IDs:
            self.fluxBox.addItem('Liquid flux (m3/s): (Q)')

        if 'QSX' in header.var_IDs and 'QSY' in header.var_IDs:
            self.fluxBox.addItem('Solid flux TOTAL (m3/s): (QSX, QSY)')
        if 'QS' in header.var_IDs:
            self.fluxBox.addItem('Solid flux TOTAL (m3/s): (QS)')
        if 'QSBLX' in header.var_IDs and 'QSBLY' in header.var_IDs:
            self.fluxBox.addItem('Solid flux BEDLOAD (m3/s): (QSBLX, QSBLY)')
        if 'QSBL' in header.var_IDs:
            self.fluxBox.addItem('Solid flux BEDLOAD (m3/s): (QSBL)')
        if 'QSSUSPX' in header.var_IDs and 'QSSUSPY' in header.var_IDs:
            self.fluxBox.addItem('Solid flux SUSPENSION (m3/s): (QSSUSPX, QSSUSPY)')
        if 'QSSUSP' in header.var_IDs:
            self.fluxBox.addItem('Solid flux SUSPENSION (m3/s): (QSSUSP)')

        for name in header.var_names:
            str_name = name.decode('utf-8').strip()
            if 'QS CLASS' in str_name:
                self.fluxBox.addItem('Solid flux TOTAL (m3/s): (%s)' % str_name)
            if 'QS BEDLOAD CL' in str_name:
                self.fluxBox.addItem('Solid flux BEDLOAD (m3/s): (%s)' % str_name)
            if 'QS SUSP. CL' in str_name:
                self.fluxBox.addItem('Solid flux SUSPENSION (m3/s): (%s)' % str_name)

        return self.fluxBox.count() > 0

    def _checkSamplingFrequency(self):
        try:
            sampling_frequency = int(self.timeSampling.text())
        except ValueError:
            QMessageBox.critical(self, 'Error', 'The sampling frequency must be a number!',
                                 QMessageBox.Ok)
            self.timeSampling.setText('1')
            return
        if sampling_frequency < 1 or sampling_frequency > len(self.time):
            QMessageBox.critical(self, 'Error', 'The sampling frequency must be in the range [1; nbFrames]!',
                                 QMessageBox.Ok)
            self.timeSampling.setText('1')
            return

    def _getFluxSection(self):
        selection = self.fluxBox.currentText()
        var_IDs = list(selection.split(':')[1].split('(')[1][:-1].split(', '))
        nb_vars = len(var_IDs)
        if nb_vars == 1:
            flux_type = FluxCalculator.LINE_INTEGRAL
        elif nb_vars == 2:
            if var_IDs[0] == 'M':
                flux_type = FluxCalculator.DOUBLE_LINE_INTEGRAL
            else:
                flux_type = FluxCalculator.LINE_FLUX
        elif nb_vars == 3:
            flux_type = FluxCalculator.AREA_FLUX
        else:
            flux_type = FluxCalculator.MASS_FLUX
        return flux_type, var_IDs, selection

    def btnOpenSerafinEvent(self):
        options = QFileDialog.Options()
        options |= QFileDialog.DontUseNativeDialog
        filename, _ = QFileDialog.getOpenFileName(self, 'Open a .slf file', '',
                                                  'Serafin Files (*.slf)', QDir.currentPath(), options=options)
        if not filename:
            return
        if not testOpen(filename):
            return

        self._reinitInput(filename)

        with Serafin.Read(filename, self.language) as resin:
            resin.read_header()

            # check if the file is 2D
            if not resin.header.is_2d:
                QMessageBox.critical(self, 'Error', 'The file type (TELEMAC 3D) is currently not supported.',
                                     QMessageBox.Ok)
                return

            flux_added = self._addFluxOptions(resin.header)
            if not flux_added:
                QMessageBox.critical(self, 'Error', 'No flux is computable from this file.',
                                     QMessageBox.Ok)
                return

            # record the time series
            resin.get_time()

            # record the mesh for future visualization and calculations
            self.parent.inDialog()
            meshLoader = LoadMeshDialog('flux', resin.header)
            self.mesh = meshLoader.run()
            self.parent.outDialog()
            if meshLoader.thread.canceled:
                self.fluxBox.clear()
                self.polygonNameBox.clear()
                return

            # update the file summary
            self.summaryTextBox.appendPlainText(resin.get_summary())

            # copy to avoid reading the same data in the future
            self.header = resin.header.copy()
            self.time = resin.time[:]

        self._resetDefaultOptions()
        self.btnOpenPolyline.setEnabled(True)
        self.parent.imageTab.reset()
        self.parent.tab.setTabEnabled(1, False)

    def btnOpenPolylineEvent(self):
        options = QFileDialog.Options()
        options |= QFileDialog.DontUseNativeDialog
        filename, _ = QFileDialog.getOpenFileName(self, 'Open a .i2s or .shp file', '',
                                                  'Polyline file (*.i2s *.shp)', options=options)
        if not filename:
            return
        if not testOpen(filename):
            return
        is_i2s = filename[-4:] == '.i2s'

        self.polylines = []
        if is_i2s:
            with BlueKenue.Read(filename) as f:
                f.read_header()
                for poly in f.get_open_polylines():
                    self.polylines.append(poly)
        else:
            try:
                for poly in Shapefile.get_open_polylines(filename):
                    self.polylines.append(poly)
            except struct.error:
                QMessageBox.critical(self, 'Error', 'Inconsistent bytes.', QMessageBox.Ok)
                return
        if not self.polylines:
            QMessageBox.critical(self, 'Error', 'The file does not contain any open polyline.',
                                 QMessageBox.Ok)
            return

        logging.info('Finished reading the polyline file %s' % filename)
        self.polygonNameBox.clear()
        self.polygonNameBox.appendPlainText(filename + '\n' + 'The file contains {} open polyline{}.'.format(
                                            len(self.polylines), 's' if len(self.polylines) > 1 else ''))
        self.csvNameBox.clear()
        self.btnSubmit.setEnabled(True)
        self.parent.imageTab.reset()
        self.parent.tab.setTabEnabled(1, False)

    def btnSubmitEvent(self):
        sampling_frequency = int(self.timeSampling.text())

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

        flux_type, self.var_IDs, flux_title = self._getFluxSection()
        self.parent.tab.setTabEnabled(1, False)

        self.csvNameBox.setText(filename)
        logging.info('Writing the output to %s' % filename)
        self.parent.inDialog()

        # initialize the progress bar
        progressBar = OutputProgressDialog()

        # do the calculations
        names = ['Section %d' % (i+1) for i in range(len(self.polylines))]

        with Serafin.Read(self.filename, self.language) as resin:
            resin.header = self.header
            resin.time = self.time
            calculator = FluxCalculatorThread(flux_type, self.var_IDs,
                                              resin, names, self.polylines, sampling_frequency,
                                              self.mesh, self.parent.csv_separator)
            progressBar.setValue(5)
            QApplication.processEvents()

            with open(filename, 'w') as f2:
                progressBar.connectToThread(calculator)
                calculator.write_csv(f2)

        if not calculator.canceled:
            progressBar.outputFinished()
        progressBar.exec_()
        self.parent.outDialog()

        if calculator.canceled:
            self.csvNameBox.clear()
            return

        # unlock the image viewer
        self.parent.imageTab.getData(flux_title)
        self.parent.tab.setTabEnabled(1, True)


class FluxPlotViewer(TemporalPlotViewer):
    def __init__(self, inputTab):
        super().__init__('section')
        self.input = inputTab

        # initialize the map for locating sections
        canvas = LineMapCanvas()
        self.map = MapViewer(canvas)

        self.setWindowTitle('Visualize the temporal evolution of volumes')

        self.flux_title = ''
        self.var_IDs = []
        self.has_map = False
        self.language = 'fr'
        self.cumulative = False

        self.locateSections = QAction('Locate sections\non map', self, icon=self.style().standardIcon(QStyle.SP_DialogHelpButton),
                                      triggered=self.locateSectionsEvent)
        self.locateSections_short = QAction('Locate sections\non map', self,
                                            icon=self.style().standardIcon(QStyle.SP_DialogHelpButton),
                                            triggered=self.locateSectionsEvent)
        self.map.closeEvent = self.enable_locate
        self.cumulativeFluxAct = QAction('Show\ncumulative flux', self, checkable=True,
                                         icon=self.style().standardIcon(QStyle.SP_DialogApplyButton))
        self.cumulativeFluxAct.toggled.connect(self.changeFluxType)

        self.toolBar.addAction(self.locateSections)
        self.toolBar.addSeparator()
        self.toolBar.addAction(self.selectColumnsAct)
        self.toolBar.addAction(self.editColumnNamesAct)
        self.toolBar.addAction(self.editColumColorAct)
        self.toolBar.addSeparator()
        self.toolBar.addAction(self.convertTimeAct)
        self.toolBar.addAction(self.changeDateAct)
        self.toolBar.addSeparator()
        self.toolBar.addAction(self.cumulativeFluxAct)

        self.mapMenu = QMenu('&Map', self)
        self.mapMenu.addAction(self.locateSections_short)
        self.polyMenu = QMenu('&Sections', self)
        self.polyMenu.addAction(self.selectColumnsAct_short)
        self.polyMenu.addAction(self.editColumnNamesAct_short)
        self.polyMenu.addAction(self.editColumColorAct_short)

        self.menuBar.addMenu(self.mapMenu)
        self.menuBar.addMenu(self.polyMenu)

    def enable_locate(self, event):
        self.locateSections.setEnabled(True)
        self.locateSections_short.setEnabled(True)

    def changeFluxType(self):
        self.cumulative = not self.cumulative
        self.current_ylabel = 'Cumulative ' + self.flux_title
        self.replot()

    def replot(self):
        self.canvas.axes.clear()
        for column in self.current_columns:
            if not self.cumulative:
                self.canvas.axes.plot(self.time[self.timeFormat], self.data[column], '-', color=self.column_colors[column],
                                      linewidth=2, label=self.column_labels[column])
            else:
                self.canvas.axes.plot(self.time[self.timeFormat], np.cumsum(self.data[column]), '-', color=self.column_colors[column],
                                      linewidth=2, label=self.column_labels[column])

        self.canvas.axes.legend()
        self.canvas.axes.grid(linestyle='dotted')
        self.canvas.axes.set_xlabel(self.current_xlabel)
        self.canvas.axes.set_ylabel(self.current_ylabel)
        self.canvas.axes.set_title(self.current_title)
        if self.timeFormat in [1, 2]:
            self.canvas.axes.set_xticklabels(self.str_datetime if self.timeFormat == 1 else self.str_datetime_bis)
            for label in self.canvas.axes.get_xticklabels():
                label.set_rotation(45)
                label.set_fontsize(8)
        self.canvas.draw()

    def getData(self, flux_title):
        self.flux_title = flux_title

        # get the new data
        csv_file = self.input.csvNameBox.text()
        self.data = pd.read_csv(csv_file, header=0, sep=self.input.parent.csv_separator)
        self.data.sort_values('time', inplace=True)

        self.var_IDs = self.input.var_IDs
        if self.input.header.date is not None:
            year, month, day, hour, minute, second = self.input.header.date
            self.start_time = datetime.datetime(year, month, day, hour, minute, second)
        else:
            self.start_time = datetime.datetime(1900, 1, 1, 0, 0, 0)
        self.datetime = list(map(lambda x: self.start_time + datetime.timedelta(seconds=x), self.data['time']))

        self.str_datetime = list(map(lambda x: x.strftime('%Y/%m/%d\n%H:%M'), self.datetime))
        self.str_datetime_bis = list(map(lambda x: x.strftime('%d/%m/%y\n%H:%M'), self.datetime))

        self.columns = list(self.data)[1:]
        self.column_labels = {x: x for x in self.columns}
        self.column_colors = {x: None for x in self.columns}
        for i in range(min(len(self.columns), len(self.defaultColors))):
            self.column_colors[self.columns[i]] = self.defaultColors[i]

        # initialize the plot
        self.time = [self.data['time'], self.data['time'], self.data['time'],
                     self.data['time'] / 60, self.data['time'] / 3600, self.data['time'] / 86400]
        self.language = self.input.language
        self.current_xlabel = self._defaultXLabel()
        self.current_ylabel = self.flux_title
        self.current_title = ''
        self.replot()

    def locateSectionsEvent(self):
        if not self.has_map:
            self.map.canvas.reinitFigure(self.input.mesh, self.input.polylines,
                                         map(self.column_labels.get, ['Section %d' % (i+1)
                                                                      for i in range(len(self.input.polylines))]),
                                         map(self.column_colors.get, [self.column_labels['Section %d' % (i+1)]
                                                                      for i in range(len(self.input.polylines))]))

            self.has_map = True
        self.locateSections.setEnabled(False)
        self.locateSections_short.setEnabled(False)
        self.map.show()

    def reset(self):
        self.has_map = False
        self.map.close()

        # reinitialize old graphical parameters
        super().reset()
        self.current_columns = ('Section 1',)


class ComputeFluxGUI(TelToolWidget):
    def __init__(self, parent=None):
        super().__init__(parent)

        self.setWindowTitle('Compute the flux of a vector field across sections')

        self.input = InputTab(self)
        self.imageTab = FluxPlotViewer(self.input)

        self.tab = QTabWidget()
        self.tab.addTab(self.input, 'Input')
        self.tab.addTab(self.imageTab, 'Visualize results')

        self.tab.setTabEnabled(1, False)
        self.tab.setStyleSheet('QTabBar::tab { height: 40px; min-width: 200px; }')

        mainLayout = QVBoxLayout()
        mainLayout.addWidget(self.tab)
        self.setLayout(mainLayout)


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
    widget = ComputeFluxGUI()
    widget.show()
    app.exec_()


