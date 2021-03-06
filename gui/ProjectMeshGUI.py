import datetime
import logging
from PyQt5.QtCore import *
from PyQt5.QtWidgets import *
import sys

from gui.util import LoadMeshDialog, OutputProgressDialog, OutputThread, \
    VariableTable, QPlainTextEditLogger, SerafinInputTab, TelToolWidget, save_dialog
import slf.misc as operations
from slf import Serafin


class ProjectMeshThread(OutputThread):
    def __init__(self, first_in, second_in, out_stream, out_header, is_inside, point_interpolators,
                 time_indices, operation_type):
        super().__init__()

        self.calculator = operations.ProjectMeshCalculator(first_in, second_in, out_header.var_IDs,
                                                           is_inside, point_interpolators,
                                                           time_indices, operation_type)
        self.out_stream = out_stream
        self.out_header = out_header
        self.nb_frames = len(time_indices)

    def run(self):
        for i, (first_time_index, second_time_index) in enumerate(self.calculator.time_indices):
            if self.canceled:
                return
            values = self.calculator.operation_in_frame(first_time_index, second_time_index)
            self.out_stream.write_entire_frame(self.out_header,
                                               self.calculator.first_in.time[first_time_index], values)

            self.tick.emit(5 + 95 * (i+1) / self.nb_frames)
            QApplication.processEvents()


class InputTab(SerafinInputTab):
    def __init__(self, parent):
        super().__init__(parent)
        self.first_data = None
        self.second_data = None
        self.second_mesh = None

        self.common_frames = []

        self.is_inside = []
        self.point_interpolators = []

        self._initWidgets()
        self._setLayout()
        self._bindEvents()

    def _initWidgets(self):
        self.btnOpen.setText('Load\nFile A')

        # create the button open the test file
        self.btnOpenSecond = QPushButton('Load\nFile B', self, icon=self.style().standardIcon(QStyle.SP_DialogOpenButton))
        self.btnOpenSecond.setToolTip('<b>Open</b> a Serafin file')
        self.btnOpenSecond.setFixedSize(105, 50)
        self.btnOpenSecond.setEnabled(False)

        # create some text fields displaying the IO files info
        self.secondNameBox = QLineEdit()
        self.secondNameBox.setReadOnly(True)
        self.secondNameBox.setFixedHeight(30)
        self.secondNameBox.setMinimumWidth(600)
        self.secondSummaryTextBox = QPlainTextEdit()
        self.secondSummaryTextBox.setReadOnly(True)
        self.secondSummaryTextBox.setMinimumHeight(40)
        self.secondSummaryTextBox.setMaximumHeight(50)
        self.secondSummaryTextBox.setMinimumWidth(600)

    def _bindEvents(self):
        self.btnOpen.clicked.connect(self.btnOpenFirstEvent)
        self.btnOpenSecond.clicked.connect(self.btnOpenSecondEvent)

    def _setLayout(self):
        mainLayout = QVBoxLayout()
        mainLayout.addItem(QSpacerItem(10, 20))
        mainLayout.setSpacing(15)

        hlayout = QHBoxLayout()
        hlayout.setAlignment(Qt.AlignLeft)
        hlayout.addItem(QSpacerItem(50, 1))
        hlayout.addWidget(self.btnOpen)
        hlayout.addWidget(self.btnOpenSecond)
        hlayout.addItem(QSpacerItem(30, 1))
        hlayout.addWidget(self.langBox)
        hlayout.setSpacing(10)
        mainLayout.addLayout(hlayout)
        mainLayout.addItem(QSpacerItem(10, 10))

        glayout = QGridLayout()
        glayout.addWidget(QLabel('     File A'), 1, 1)
        glayout.addWidget(self.inNameBox, 1, 2)
        glayout.addWidget(QLabel('     Summary'), 2, 1)
        glayout.addWidget(self.summaryTextBox, 2, 2)
        glayout.addWidget(QLabel('     File B'), 3, 1)
        glayout.addWidget(self.secondNameBox, 3, 2)
        glayout.addWidget(QLabel('     Summary'), 4, 1)
        glayout.addWidget(self.secondSummaryTextBox, 4, 2)
        glayout.setAlignment(Qt.AlignLeft)
        glayout.setVerticalSpacing(10)
        mainLayout.addLayout(glayout)

        mainLayout.addItem(QSpacerItem(10, 15))
        mainLayout.addWidget(QLabel('   Message logs'))
        mainLayout.addWidget(self.logTextBox.widget)
        self.setLayout(mainLayout)
        self.setLayout(mainLayout)

    def _reinitFirst(self):
        self.reset()
        self.first_data = None
        self.btnOpenSecond.setEnabled(False)

    def _reinitSecond(self, filename):
        self.second_data = None
        self.secondNameBox.setText(filename)
        self.secondSummaryTextBox.clear()
        self.parent.reset()

    def _reinitCommonFrames(self):
        first_frames = list(map(lambda x: self.first_data.start_time
                                                + datetime.timedelta(seconds=x), self.first_data.time))
        second_frames = list(map(lambda x: self.second_data.start_time
                                                + datetime.timedelta(seconds=x), self.second_data.time))
        self.common_frames = []
        for first_index, first_frame in enumerate(first_frames):
            for second_index, second_frame in enumerate(second_frames):
                if first_frame == second_frame:
                    self.common_frames.append((first_index, second_index))

    def btnOpenFirstEvent(self):
        canceled, filename = super().open_event()
        if canceled:
            return

        self._reinitFirst()

        success, self.first_data = self.read_2d(filename)
        if not success:
            return

        self.btnOpenSecond.setEnabled(True)

        if self.second_data is not None:
            keep_second = self.parent.resetFirst()
            if not keep_second:
                self._reinitSecond('')
            else:
                self._reinitCommonFrames()
                if not self.common_frames:
                    self._reinitSecond('')
                else:
                    self.parent.getSecond(True, [])

    def btnOpenSecondEvent(self):
        canceled, filename = super().open_event()
        if canceled:
            return

        self._reinitSecond(filename)
        self.secondNameBox.setText(filename)

        success, self.second_data = self.read_2d(filename, update=False)
        if not success:
            return

        # check if the second file has common variables with the first file
        common_vars = [(var_ID, var_names, var_unit) for var_ID, var_names, var_unit
                       in zip(self.first_data.header.var_IDs, self.first_data.header.var_names,
                              self.first_data.header.var_units)
                       if var_ID in self.second_data.header.var_IDs]
        if not common_vars:
            self.second_data = None
            QMessageBox.critical(self, 'Error', 'No common variable with file A.',
                                 QMessageBox.Ok)
            return

        # check if the second file has common frames with the first file
        self._reinitCommonFrames()

        if not self.common_frames:
            QMessageBox.critical(self, 'Error', 'No common frame with file A.', QMessageBox.Ok)
            self.second_data = None
            return

        # record the mesh
        self.parent.inDialog()
        meshLoader = LoadMeshDialog('interpolation', self.second_data.header)
        self.second_mesh = meshLoader.run()

        # locate all points of the first mesh in the second mesh
        self.is_inside, self.point_interpolators \
            = self.second_mesh.get_point_interpolators(list(zip(self.first_data.header.x, self.first_data.header.y)))
        self.parent.outDialog()
        if meshLoader.thread.canceled:
            self.second_data = None
            return

        # update the file summary
        self.secondSummaryTextBox.appendPlainText(self.second_data.header.summary())
        self.parent.getSecond(False, common_vars)


class SubmitTab(QWidget):
    def __init__(self, inputTab, parent):
        super().__init__()
        self.input = inputTab
        self.parent = parent

        self._initWidgets()
        self._bindEvents()
        self._setLayout()

    def _initWidgets(self):
        # create a text field for mesh intersection info display
        self.infoBox = QPlainTextEdit()
        self.infoBox.setFixedHeight(60)
        self.infoBox.setReadOnly(True)

        # create two 3-column tables for variables selection
        self.firstTable = VariableTable()
        self.secondTable = VariableTable()

        # create combo box widgets for choosing the operation
        self.operationBox = QComboBox()
        self.operationBox.setFixedSize(400, 30)
        for op in ['Project B on A', 'B - A', 'A - B', 'max(A, B)', 'min(A, B)']:
            self.operationBox.addItem(op)

        # create the widget displaying message logs
        self.logTextBox = QPlainTextEditLogger(self)
        self.logTextBox.setFormatter(logging.Formatter('%(asctime)s - [%(levelname)s] - \n%(message)s'))
        logging.getLogger().addHandler(self.logTextBox)
        logging.getLogger().setLevel(self.parent.logging_level)

        # create a check box for output file format (simple or double precision)
        self.singlePrecisionBox = QCheckBox('Convert to SERAFIN \n(single precision)', self)
        self.singlePrecisionBox.setEnabled(False)

        # create the submit button
        self.btnSubmit = QPushButton('Submit', self, icon=self.style().standardIcon(QStyle.SP_DialogSaveButton))
        self.btnSubmit.setToolTip('<b>Submit</b> to write a Serafin output')
        self.btnSubmit.setFixedSize(105, 50)

    def _bindEvents(self):
        self.btnSubmit.clicked.connect(self.btnSubmitEvent)

    def _setLayout(self):
        mainLayout = QVBoxLayout()
        mainLayout.addItem(QSpacerItem(1, 10))
        mainLayout.addWidget(self.infoBox)
        mainLayout.addItem(QSpacerItem(1, 15))
        hlayout = QHBoxLayout()
        hlayout.addItem(QSpacerItem(30, 1))

        vlayout = QVBoxLayout()
        lb = QLabel('Available variables')
        vlayout.addWidget(lb)
        vlayout.setAlignment(lb, Qt.AlignHCenter)
        vlayout.addWidget(self.firstTable)
        hlayout.addLayout(vlayout)
        hlayout.addItem(QSpacerItem(15, 1))

        vlayout = QVBoxLayout()
        lb = QLabel('Output variables')
        vlayout.addWidget(lb)
        vlayout.setAlignment(lb, Qt.AlignHCenter)
        vlayout.addWidget(self.secondTable)

        hlayout.addLayout(vlayout)
        hlayout.addItem(QSpacerItem(30, 1))

        mainLayout.addLayout(hlayout)
        mainLayout.addItem(QSpacerItem(50, 20))
        glayout = QGridLayout()
        glayout.addWidget(QLabel('     Select an operation'), 1, 1)
        glayout.addWidget(self.operationBox, 1, 2)
        glayout.setSpacing(10)
        mainLayout.addLayout(glayout)
        mainLayout.setAlignment(glayout, Qt.AlignTop | Qt.AlignLeft)
        mainLayout.addItem(QSpacerItem(50, 20))

        hlayout = QHBoxLayout()
        hlayout.addItem(QSpacerItem(50, 10))
        hlayout.addWidget(self.btnSubmit)
        hlayout.addItem(QSpacerItem(50, 10))
        hlayout.addWidget(self.singlePrecisionBox)
        hlayout.addItem(QSpacerItem(50, 10))
        mainLayout.addLayout(hlayout)
        mainLayout.addItem(QSpacerItem(30, 15))
        mainLayout.addWidget(QLabel('   Message logs'))
        mainLayout.addWidget(self.logTextBox.widget)
        self.setLayout(mainLayout)

    def _initVarTables(self, common_vars):
        for i, (var_ID, var_name, var_unit) in enumerate(common_vars):
            self.firstTable.insertRow(self.firstTable.rowCount())
            id_item = QTableWidgetItem(var_ID.strip())
            name_item = QTableWidgetItem(var_name.decode('utf-8').strip())
            unit_item = QTableWidgetItem(var_unit.decode('utf-8').strip())
            self.firstTable.setItem(i, 0, id_item)
            self.firstTable.setItem(i, 1, name_item)
            self.firstTable.setItem(i, 2, unit_item)

    def _getSelectedVariables(self):
        return self.secondTable.get_selected_all()

    def _getOutputHeader(self):
        selected_vars = self._getSelectedVariables()
        output_header = self.input.first_data.header.copy()
        output_header.nb_var = len(selected_vars)
        output_header.var_IDs, output_header.var_names, output_header.var_units = [], [], []
        for var_ID, var_name, var_unit in selected_vars:
            output_header.var_IDs.append(var_ID)
            output_header.var_names.append(var_name)
            output_header.var_units.append(var_unit)
        if self.singlePrecisionBox.isChecked():
            output_header.to_single_precision()
        return output_header

    def _updateInfo(self):
        self.infoBox.clear()
        self.infoBox.appendPlainText('The two files has {} common variables and {} common frames.\n'
                                     'The mesh A has {} / {} nodes inside the mesh B.'.format(
                                     self.firstTable.rowCount() + self.secondTable.rowCount(),
                                     len(self.input.common_frames),
                                     sum(self.input.is_inside), self.input.first_data.header.nb_nodes))

    def reset(self):
        self.firstTable.setRowCount(0)
        self.secondTable.setRowCount(0)
        self.singlePrecisionBox.setChecked(False)
        self.singlePrecisionBox.setEnabled(False)

    def resetFirst(self):
        common_vars = [(var_ID, var_name, var_unit) for var_ID, var_name, var_unit
                       in zip(self.input.first_data.header.var_IDs, self.input.first_data.header.var_names,
                              self.input.first_data.header.var_units)
                       if var_ID in self.input.second_data.header.var_IDs]
        if not common_vars:
            self.firstTable.setRowCount(0)
            self.secondTable.setRowCount(0)
            return False
        else:
            # recover, if possible, old variable selection
            old_selected = self._getSelectedVariables()
            self.firstTable.setRowCount(0)
            self.secondTable.setRowCount(0)

            self._initVarTables([(var_ID, var_name, var_unit) for var_ID, var_name, var_unit in common_vars
                                 if var_ID not in old_selected])
            for var_ID, var_name, var_unit in common_vars:
                if var_ID in old_selected:
                    row = self.secondTable.rowCount()
                    self.secondTable.insertRow(row)
                    id_item = QTableWidgetItem(var_ID.strip())
                    name_item = QTableWidgetItem(var_name.decode('utf-8').strip())
                    unit_item = QTableWidgetItem(var_unit.decode('utf-8').strip())
                    self.secondTable.setItem(row, 0, id_item)
                    self.secondTable.setItem(row, 1, name_item)
                    self.secondTable.setItem(row, 2, unit_item)
            return True

    def getSecond(self, old_second, common_vars):
        if not old_second:
            self.firstTable.setRowCount(0)
            self.secondTable.setRowCount(0)
            self._initVarTables(common_vars)
            if self.input.first_data.header.is_double_precision():
                self.singlePrecisionBox.setEnabled(True)
        self._updateInfo()

    def btnSubmitEvent(self):
        if self.secondTable.rowCount() == 0:
            QMessageBox.critical(self, 'Error', 'Choose at least one output variable before submit!',
                                 QMessageBox.Ok)
            return

        canceled, filename = save_dialog('Serafin', input_names=[self.input.first_data.filename,
                                                                 self.input.second_data.filename])
        if canceled:
            return

        # deduce header from selected variable IDs
        output_header = self._getOutputHeader()
        time_indices = self.input.common_frames
        operation_type = {0: operations.PROJECT, 1: operations.DIFF, 2: operations.REV_DIFF,
                          3: operations.MAX_BETWEEN, 4: operations.MIN_BETWEEN}[self.operationBox.currentIndex()]
        self.parent.inDialog()
        progressBar = OutputProgressDialog()

        # do some calculations
        with Serafin.Read(self.input.first_data.filename, self.input.first_data.language) as first_in:
            first_in.header = self.input.first_data.header
            first_in.time = self.input.first_data.time

            with Serafin.Read(self.input.second_data.filename, self.input.second_data.language) as second_in:
                second_in.header = self.input.second_data.header
                second_in.time = self.input.second_data.time

                progressBar.setValue(5)
                QApplication.processEvents()

                with Serafin.Write(filename, self.input.first_data.language) as out_stream:

                    out_stream.write_header(output_header)
                    process = ProjectMeshThread(first_in, second_in, out_stream, output_header, self.input.is_inside,
                                                self.input.point_interpolators, time_indices,
                                                operation_type)
                    progressBar.connectToThread(process)
                    process.run()

                    if not process.canceled:
                        progressBar.outputFinished()

        progressBar.exec_()
        self.parent.outDialog()


class ProjectMeshGUI(TelToolWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Compute the difference between two meshes')
        self.tab = QTabWidget()
        self.tab.setStyleSheet('QTabBar::tab { height: 40px; min-width: 150px; }')

        self.input = InputTab(self)
        self.submit = SubmitTab(self.input, self)

        self.tab.addTab(self.input, 'Input')
        self.tab.addTab(self.submit, 'Submit')
        self.tab.setTabEnabled(1, False)

        mainLayout = QVBoxLayout()
        mainLayout.addWidget(self.tab)
        self.setLayout(mainLayout)

    def reset(self):
        self.submit.reset()
        self.tab.setTabEnabled(1, False)

    def resetFirst(self):
        keep_old = self.submit.resetFirst()
        if not keep_old:
            self.tab.setTabEnabled(1, False)
        return keep_old

    def getSecond(self, old_second, common_vars):
        self.submit.getSecond(old_second, common_vars)
        self.tab.setTabEnabled(1, True)


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
    widget = ProjectMeshGUI()
    widget.show()
    app.exec_()



