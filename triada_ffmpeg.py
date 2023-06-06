# pylint: disable=missing-module-docstring
# pylint: disable=missing-class-docstring
# pylint: disable=missing-function-docstring
# pylint: disable=no-name-in-module
# pylint: disable=unnecessary-lambda

import sys
import os
import re
import subprocess
import glob

from PyQt5.QtWidgets import QApplication, QWidget, QVBoxLayout, QHBoxLayout
from PyQt5.QtWidgets import QPushButton, QRadioButton, QSlider, QProgressBar
from PyQt5.QtWidgets import QFileDialog, QLabel, QComboBox, QLineEdit, QSpinBox
from PyQt5.QtWidgets import QGraphicsOpacityEffect, QTextEdit, QCheckBox, QMessageBox
from PyQt5.QtWidgets import QButtonGroup, QFrame, QGridLayout
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QStandardPaths, QObject
from PyQt5.QtGui import QTextCursor

import ffmpeg

VERSION = "0.3.5"


class EncoderThread(QThread):
    progress = pyqtSignal(int)

    def __init__(self, cmd):
        super().__init__()
        self.cmd = cmd
        self.stop_flag = False

    def run(self):
        process = subprocess.Popen(self.cmd,
                                   stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT,
                                   universal_newlines=True,
                                   shell=True)

        for line in process.stdout:
            print(line.strip())
            progress_match = re.search(r'frame=\s*(\d+)', line)

            if progress_match:
                frame_number = int(progress_match.group(1))
                self.progress.emit(frame_number)

            if self.stop_flag:  # check stop flag
                process.stdin.write('q')  # send "q" keypress to stop
                process.stdin.flush()
                break

        process.wait()

    def stop(self):
        self.stop_flag = True


class DnDLineEdit(QLineEdit):
    file_dropped = pyqtSignal(str)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event):  # pylint: disable=invalid-name
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):  # pylint: disable=invalid-name
        url = event.mimeData().urls()[0].toLocalFile()
        self.setText(url)
        self.file_dropped.emit(url)


class CustomStream(QObject):
    message_written = pyqtSignal(str)

    def write(self, text):
        self.message_written.emit(str(text))

    def flush(self):
        pass


class FFmpegGUI(QWidget):
    encoder_thread = None
    video_file_info = None
    output_base_name = None
    custom_stream = None

    def __init__(self):
        super().__init__()

        self.original_stdout = sys.stdout
        self.init_ui()

    def init_ui(self):
        self.setWindowTitle(f"Triada FFmpeg GUI v{VERSION}")
        # Set initial window size
        self.resize(300, self.height())

        layout = QVBoxLayout()

        layout.addWidget(QLabel('Video or Image Sequence'))
        self.video_input = DnDLineEdit()
        self.video_input.file_dropped.connect(self.select_video)
        self.video_input.editingFinished.connect(
            lambda: self.select_video(self.video_input.text()))
        self.video_button = QPushButton('Browse')
        self.video_button.clicked.connect(lambda: self.select_video())
        video_layout = QHBoxLayout()
        video_layout.addWidget(self.video_input)
        video_layout.addWidget(self.video_button)
        layout.addLayout(video_layout)

        self.frame_rate_label = QLabel('Frame Rate')
        self.frame_rate_input = QSpinBox()
        # Set the minimum and maximum frame rate values
        self.frame_rate_input.setRange(1, 240)
        # Set the default frame rate value to 30
        self.frame_rate_input.setValue(30)
        layout.addWidget(self.frame_rate_label)
        layout.addWidget(self.frame_rate_input)
        # Set initially hidden until the image sequence is detected
        self.frame_rate_label.hide()
        self.frame_rate_input.hide()

        layout.addWidget(QLabel('Audio Source (optional)'))
        self.audio_input = DnDLineEdit()
        self.audio_input.file_dropped.connect(self.select_audio)
        self.audio_input.editingFinished.connect(
            lambda: self.select_audio(self.audio_input.text()))
        self.audio_button = QPushButton('Browse')
        self.audio_button.clicked.connect(lambda: self.select_audio())
        audio_layout = QHBoxLayout()
        audio_layout.addWidget(self.audio_input)
        audio_layout.addWidget(self.audio_button)
        layout.addLayout(audio_layout)

        layout.addWidget(QLabel('Resize'))
        resize_layout = QHBoxLayout()
        self.resize_width = QSpinBox()
        self.resize_width.setFixedWidth(64)
        self.resize_width.setRange(0, 8192)
        self.resize_width.setSingleStep(8)
        self.resize_width.setValue(0)
        resize_layout.addWidget(self.resize_width)
        resize_layout.addWidget(QLabel('x'))
        self.resize_height = QSpinBox()
        self.resize_height.setFixedWidth(64)
        self.resize_height.setRange(0, 8192)
        self.resize_height.setSingleStep(8)
        self.resize_height.setValue(0)
        resize_layout.addWidget(self.resize_height)
        resize_layout.addStretch(1)
        self.resize_width.valueChanged.connect(self.on_resize_changed)
        self.resize_height.valueChanged.connect(self.on_resize_changed)
        layout.addLayout(resize_layout)

        self.resize_filter_label = QLabel('Resize Filter')
        self.resize_filter_combo = QComboBox()
        self.resize_filter_combo.addItem('bicubic')
        self.resize_filter_combo.addItem('lanczos')
        self.resize_filter_combo.addItem('spline')
        # Set the default value to "lanczos"
        self.resize_filter_combo.setCurrentText('lanczos')
        layout.addWidget(self.resize_filter_label)
        layout.addWidget(self.resize_filter_combo)

        layout.addWidget(QLabel('Codec'))
        self.codec_combo = QComboBox()
        self.codec_combo.addItems(['x264', 'x265', 'ProRes'])
        layout.addWidget(self.codec_combo)
        self.codec_combo.setCurrentIndex(1)  # Set the x265 codec as default
        self.codec_combo.currentIndexChanged.connect(self.on_codec_changed)

        # x264/x265 pixel format radio buttons
        self.pixel_format_frame = QFrame()
        pixel_format_layout = QVBoxLayout(self.pixel_format_frame)
        pixel_format_layout.setContentsMargins(0, 0, 0, 0)
        pixel_format_layout.addWidget(QLabel('Pixel Format'))

        self.pixel_format_buttons = self.create_radio_button_group(
            ['8-bit 4:2:0', '10-bit 4:2:0', '10-bit 4:2:2'],
            default_index=1,
            layout=pixel_format_layout,
            callback=self.update_output_file_name)

        # ProRes profile radio buttons
        self.prores_profile_frame = QFrame()
        prores_profile_layout = QGridLayout(self.prores_profile_frame)
        prores_profile_layout.addWidget(QLabel('Profile'), 0, 0, 1, -1)
        prores_profile_layout.setContentsMargins(0, 0, 0, 0)

        self.prores_profile_buttons = self.create_radio_button_group(
            ['proxy', 'lt', 'standart', 'hq', '4444', '4444hq'],
            default_index=2,
            layout=prores_profile_layout,
            callback=self.update_output_file_name,
            row_count=3)

        # Add to main layout
        layout.addWidget(self.pixel_format_frame)
        layout.addWidget(self.prores_profile_frame)

        self.crf_label = QLabel()
        layout.addWidget(self.crf_label)
        self.crf_slider = QSlider(Qt.Horizontal)
        self.crf_slider.setRange(1, 32)
        self.crf_slider.setValue(16)
        self.crf_slider.setTickPosition(QSlider.TicksBelow)
        self.crf_slider.setTickInterval(1)
        self.crf_slider.valueChanged.connect(self.update_crf_label)
        layout.addWidget(self.crf_slider)
        self.update_crf_label(self.crf_slider.value())

        self.audio_frame = QFrame()
        audio_layout = QVBoxLayout(self.audio_frame)
        audio_layout.setContentsMargins(0, 0, 0, 0)
        self.audio_codec_combo = QComboBox()
        self.audio_codec_combo.addItem("AAC")
        self.audio_codec_combo.addItem("PCM 16-bit")
        self.audio_codec_combo.addItem("PCM 24-bit")
        audio_layout.addWidget(QLabel('Audio Codec'))
        audio_layout.addWidget(self.audio_codec_combo)

        self.audio_codec_combo.currentTextChanged.connect(
            lambda codec: self.audio_bitrate_input.setEnabled(codec == "AAC"))

        audio_layout.addWidget(QLabel('Audio Bitrate (kbps)'))
        self.audio_bitrate_input = QSpinBox()
        self.audio_bitrate_input.setFixedWidth(64)
        self.audio_bitrate_input.setRange(32, 512)
        self.audio_bitrate_input.setSingleStep(64)
        self.audio_bitrate_input.setValue(320)
        audio_layout.addWidget(self.audio_bitrate_input)

        self.audio_direct_stream_copy = QCheckBox('direct stream copy')
        audio_layout.addWidget(self.audio_direct_stream_copy)

        self.audio_direct_stream_copy.stateChanged.connect(
            lambda state: (
                self.audio_codec_combo.setEnabled(state == Qt.Unchecked),
                self.audio_bitrate_input.setEnabled(state == Qt.Unchecked)
            )
        )

        layout.addWidget(self.audio_frame)

        layout.addWidget(QLabel('Preset'))
        self.preset_combo = QComboBox()
        self.preset_combo.addItems(
            ['veryfast', 'faster', 'fast', 'medium', 'slow', 'slower', 'veryslow'])
        layout.addWidget(self.preset_combo)
        # Set the 'slow' preset as default
        self.preset_combo.setCurrentIndex(4)

        layout.addWidget(QLabel('Output Folder'))
        self.output_folder_input = QLineEdit()
        self.output_button = QPushButton('Browse')
        self.output_button.clicked.connect(self.select_output_folder)
        output_layout = QHBoxLayout()
        output_layout.addWidget(self.output_folder_input)
        output_layout.addWidget(self.output_button)
        layout.addLayout(output_layout)
        default_output_folder = QStandardPaths.writableLocation(
            QStandardPaths.DocumentsLocation)
        self.output_folder_input.setText(default_output_folder)

        layout.addWidget(QLabel('Output File'))
        self.output_file_input = QLineEdit()
        layout.addWidget(self.output_file_input)

        self.encode_button = QPushButton('Start')
        self.encode_button.setMinimumSize(0, 32)
        self.encode_button.clicked.connect(self.encode_video)

        self.stop_button = QPushButton('Stop', self)
        self.stop_button.setMinimumSize(0, 32)
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self.stop_encoding)

        button_layout = QHBoxLayout()
        button_layout.addWidget(self.encode_button)
        button_layout.addWidget(self.stop_button)
        layout.addLayout(button_layout)

        self.progress_bar = QProgressBar()
        layout.addWidget(self.progress_bar)
        self.progress_bar_opacity = QGraphicsOpacityEffect(self.progress_bar)
        self.progress_bar.setGraphicsEffect(self.progress_bar_opacity)
        self.progress_bar_opacity.setOpacity(0.0)

        self.show_console_output_checkbox = QCheckBox("Show console output")
        layout.addWidget(self.show_console_output_checkbox)

        self.console_output = QTextEdit()
        self.console_output.setReadOnly(True)
        layout.addWidget(self.console_output)
        self.console_output.hide()
        self.show_console_output_checkbox.stateChanged.connect(self.toggle_console_output)

        self.setLayout(layout)

        self.on_resize_changed()
        self.on_codec_changed(self.codec_combo.currentIndex())

    @staticmethod
    def create_radio_button_group(labels, default_index, layout, callback, row_count=1):
        button_group = QButtonGroup()
        radio_buttons = []

        for i, label in enumerate(labels):
            radio_button = QRadioButton(label)
            button_group.addButton(radio_button)
            radio_button.toggled.connect(callback)

            if row_count > 1:
                # Compute grid position
                position = (i % row_count + 1, i // row_count)
                layout.addWidget(radio_button, *position)
            else:  # Default to vertical layout
                layout.addWidget(radio_button)

            radio_buttons.append(radio_button)

        radio_buttons[default_index].setChecked(True)

        return radio_buttons

    def toggle_console_output(self, state):
        if state == Qt.Checked:
            self.console_output.show()
            self.custom_stream = CustomStream()
            self.custom_stream.message_written.connect(self.append_to_console)
            sys.stdout = self.custom_stream
        else:
            self.console_output.hide()
            sys.stdout = self.original_stdout

    def append_to_console(self, text):
        self.console_output.moveCursor(QTextCursor.End)
        self.console_output.insertPlainText(text)
        self.console_output.moveCursor(QTextCursor.End)

    def update_output_file_name(self):
        if self.output_base_name is not None:
            size_prefix = ''
            if self.resize_width.value() > 0 or self.resize_height.value() > 0:
                width = self.resize_width.value()
                height = self.resize_height.value()
                if height > 0 and width > 0:
                    size_prefix = f"{width}x{height}"
                elif width > 0:
                    size_prefix = f"{width}w"
                elif height > 0:
                    size_prefix = f"{height}p"

            codec_name = self.codec_combo.currentText().lower()
            if codec_name == "prores":
                container = "mov"
                pix_fmt_name = self.prores_profile_buttons[self.get_prores_profile_index(
                )].text().lower()
            else:
                container = "mp4"
                pix_fmt_name = ('', '10bit', '10bit422')[self.get_pixel_format_index()]
            crf = self.crf_slider.value()
            output_file_name = (
                f"{self.output_base_name} "
                f"{size_prefix + '_' if size_prefix else ''}"
                f"{codec_name}"
                f"{'_' + pix_fmt_name if pix_fmt_name else ''}"
                f"_q{crf}.{container}"
            )
            self.output_file_input.setText(output_file_name)

    def on_resize_changed(self):
        enable_filter = self.resize_width.value() > 0 or self.resize_height.value() > 0
        self.resize_filter_label.setEnabled(enable_filter)
        self.resize_filter_combo.setEnabled(enable_filter)
        self.update_output_file_name()

    def on_codec_changed(self, index):
        codec = self.codec_combo.itemText(index).lower()
        if codec == 'x264' or codec == 'x265':
            self.prores_profile_frame.hide()
            self.pixel_format_frame.show()
            if codec == 'x264':
                self.pixel_format_buttons[0].setChecked(True)
            else:
                self.pixel_format_buttons[1].setChecked(True)
        elif codec == 'prores':
            self.pixel_format_frame.hide()
            self.prores_profile_frame.show()
        self.update_output_file_name()

    def update_crf_label(self, value):
        self.crf_label.setText(f"Quality (CRF): {value}")
        self.update_output_file_name()

    def select_video(self, video_file=None):
        if video_file is None:
            video_file, _ = QFileDialog.getOpenFileName()

        if video_file:
            # Check if the selected file is an image file (e.g., *00000.png or *%0*d.png)
            match = re.match(r'^(.*?)(?:(\d+)|%(\d+)d)\.(png|jpg|jpeg|tiff)$',
                             os.path.basename(video_file), re.IGNORECASE)
            if match:
                # Get the prefix, the number of digits in the frame number, and the file extension
                prefix, frame_number, percentage, file_ext = match.groups()
                if frame_number:
                    num_digits = len(frame_number)
                elif percentage:
                    num_digits = int(percentage)

                video_file = os.path.join(os.path.dirname(
                    video_file), f"{prefix}%0{num_digits}d.{file_ext}")

                # Set the default output file name based on the input file name
                self.output_base_name = prefix.rstrip('_')
                # Show the frame rate selector
                self.frame_rate_label.show()
                self.frame_rate_input.show()
            else:
                # Set the default output file name based on the input file name
                self.output_base_name = os.path.splitext(
                    os.path.basename(video_file))[0]

                # Hide the frame rate selector
                self.frame_rate_label.hide()
                self.frame_rate_input.hide()

            self.video_input.setText(video_file)
            self.video_file_info = self.get_file_info(video_file)
            self.update_output_file_name()

    def select_audio(self, audio_file=None):
        if audio_file is None:
            audio_file, _ = QFileDialog.getOpenFileName()

        if audio_file:
            self.audio_input.setText(audio_file)

    def select_output_folder(self):
        output_folder = QFileDialog.getExistingDirectory(
            self, "Select Output Folder")

        if output_folder:
            self.output_folder_input.setText(output_folder)

    @staticmethod
    def get_file_info(file_path):
        try:
            frame_count = 0
            duration = None
            audio_stream_count = 0
            is_rgb = False

            # Check if the input is an image sequence
            if '%' in file_path:
                # Replace the %0xd part of the pattern with a wildcard
                pattern = re.sub(r'%0\d+d', '*', file_path)
                # Count the number of matching files in the directory
                frame_count = len(glob.glob(pattern))
                is_rgb = True
            else:
                streams = ffmpeg.probe(file_path)["streams"]
                for stream in streams:
                    if stream['codec_type'] == 'video':
                        pixel_format = stream['pix_fmt']
                        is_rgb = pixel_format == "rgb" or pixel_format == "gbrp"
                        frame_count = int(stream['nb_frames'])
                        duration = float(stream['duration'])

                    if stream['codec_type'] == 'audio':
                        audio_stream_count += 1

            return {
                'frame_count': frame_count,
                'duration': duration,
                'audio_stream_count': audio_stream_count,
                'is_rgb': is_rgb,
            }

        except (ffmpeg.Error, KeyError, StopIteration):
            return None

    def get_pixel_format_index(self):
        return next((i for i, button in enumerate(self.pixel_format_buttons)
                     if button.isChecked()), "0")

    def get_prores_profile_index(self):
        return next((i for i, button in enumerate(self.prores_profile_buttons)
                     if button.isChecked()), "0")

    def check_file_overwrite(self, output_file):
        if os.path.exists(output_file):
            reply = QMessageBox.question(
                self, "Overwrite existing file?",
                f"The file '{output_file}' already exists. Do you want to overwrite it?",
                QMessageBox.Yes | QMessageBox.No)

            if reply == QMessageBox.No:
                return False

        return True

    def encode_video(self):
        video_file = self.video_input.text()

        if not video_file:
            return

        output_file = os.path.join(self.output_folder_input.text(),
                                   self.output_file_input.text())

        if not self.check_file_overwrite(output_file):
            return

        audio_file = self.audio_input.text()
        crf = self.crf_slider.value()

        codec = {
            "x264": "libx264",
            "x265": "libx265",
            "ProRes": "prores_ks"
        }[self.codec_combo.currentText()]

        if codec == "prores_ks":
            pix_fmt = 'yuv444p10' if self.get_prores_profile_index() >= 4 else 'yuv422p10'
        else:
            pix_fmt = ('yuv420p', 'yuv420p10', 'yuv422p10')[self.get_pixel_format_index()]

        colorspace = "bt709"
        preset = self.preset_combo.currentText()

        ffmpeg_args = {
            "vcodec": codec,
            "pix_fmt": pix_fmt,
            "colorspace": colorspace,
            "color_trc": colorspace,
            "color_primaries": colorspace,
            "y": None  # force overwrite
        }

        if codec == "libx264" or codec == "libx265":
            ffmpeg_args["crf"] = crf
            ffmpeg_args["preset"] = preset
            ffmpeg_args["movflags"] = "faststart"

        elif codec == "prores_ks":
            ffmpeg_args["profile:v"] = self.get_prores_profile_index()
            ffmpeg_args["q:v"] = crf
            ffmpeg_args["vendor"] = "ap10"

        audio_bitrate = self.audio_bitrate_input.value()

        frame_count = self.video_file_info['frame_count']
        video_file_has_audio = self.video_file_info['audio_stream_count'] > 0
        input_is_rgb = self.video_file_info['is_rgb']

        audio = None
        if '%' in video_file:
            frame_rate = self.frame_rate_input.value()
            input_stream = ffmpeg.input(
                video_file, format='image2', framerate=frame_rate)
            video_duration = frame_count / frame_rate
        else:
            input_stream = ffmpeg.input(video_file)
            video_duration = self.video_file_info['duration']
            if video_file_has_audio:
                audio = input_stream.audio

        video = input_stream.video

        resize_width = self.resize_width.value()
        resize_height = self.resize_height.value()
        resize_filter = self.resize_filter_combo.currentText()

        if resize_width > 0 or resize_height > 0:
            resize_width = resize_width if resize_width > 0 else -1
            resize_height = resize_height if resize_height > 0 else -1

            if input_is_rgb:
                video = video.filter('scale', resize_width, resize_height, sws_flags=resize_filter,
                                     in_color_matrix='bt601', out_color_matrix='bt709')
            else:
                video = video.filter(
                    'scale', resize_width, resize_height, sws_flags=resize_filter)
        elif input_is_rgb:
            video = video.filter(
                'scale', in_color_matrix='bt601', out_color_matrix='bt709')

        if audio_file:
            audio = ffmpeg.input(audio_file).audio
            if not self.audio_direct_stream_copy.isChecked():
                audio = audio.filter_('atrim', duration=video_duration)

        if audio is not None:
            audio_codec = 'copy' if self.audio_direct_stream_copy.isChecked() else (
                'aac', 'pcm_s16le', 'pcm_s24le')[self.audio_codec_combo.currentIndex()]
            ffmpeg_args["acodec"] = audio_codec
            if audio_codec == 'aac':
                ffmpeg_args["ab"] = f"{audio_bitrate}k"
            output = ffmpeg.output(video, audio, output_file, **ffmpeg_args)
        else:
            output = ffmpeg.output(video, output_file, **ffmpeg_args)

        try:
            cmd = ffmpeg.compile(output)

            print("FFmpeg command:", " ".join(cmd))

            self.encoder_thread = EncoderThread(cmd)
            self.encoder_thread.progress.connect(self.update_progress)
            self.encoder_thread.finished.connect(self.encoding_finished)

            self.encode_button.setEnabled(False)
            self.stop_button.setEnabled(True)
            self.progress_bar.setMaximum(frame_count)
            self.progress_bar_opacity.setOpacity(1.0)

            self.encoder_thread.start()

        except ffmpeg.Error as error:
            print(error.stderr.decode())

    def stop_encoding(self):
        self.stop_button.setEnabled(False)
        self.encoder_thread.stop()

    def update_progress(self, frame_number):
        self.progress_bar.setValue(frame_number)

    def encoding_finished(self):
        self.progress_bar.reset()
        self.encode_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.progress_bar_opacity.setOpacity(0.0)
        print("Encoding finished")


if __name__ == '__main__':
    app = QApplication(sys.argv)
    window = FFmpegGUI()
    window.show()
    sys.exit(app.exec_())
