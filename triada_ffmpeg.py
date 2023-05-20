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
from PyQt5.QtWidgets import QGraphicsOpacityEffect
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QStandardPaths

import ffmpeg


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
                                   universal_newlines=True)

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


class FFmpegGUI(QWidget):
    encoder_thread = None
    video_file_info = None
    output_base_name = None

    def __init__(self):
        super().__init__()

        self.init_ui()

    def init_ui(self):
        self.setWindowTitle('Triada FFmpeg GUI')

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

        layout.addWidget(QLabel('Codec'))
        self.codec_combo = QComboBox()
        self.codec_combo.addItems(['libx264', 'libx265'])
        layout.addWidget(self.codec_combo)

        layout.addWidget(QLabel('Pixel Format'))
        self.pix_fmt_8bit = QRadioButton('8-bit (yuv420p)')
        self.pix_fmt_10bit = QRadioButton('10-bit (yuv420p10le)')
        layout.addWidget(self.pix_fmt_8bit)
        layout.addWidget(self.pix_fmt_10bit)
        self.pix_fmt_8bit.setChecked(True)

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
        self.setLayout(layout)

    def update_crf_label(self, value):
        self.crf_label.setText(f"Quality (CRF): {value}")

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

    def encode_video(self):
        video_file = self.video_input.text()
        audio_file = self.audio_input.text()
        crf = self.crf_slider.value()
        codec = self.codec_combo.currentText()
        pix_fmt = 'yuv420p' if self.pix_fmt_8bit.isChecked() else 'yuv420p10le'
        colorspace = "bt709"

        if not video_file:
            return

        output_file = os.path.join(self.output_folder_input.text(),
                                   f"{self.output_base_name} {codec}_{pix_fmt}.mp4")

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

        if input_is_rgb:
            video = video.filter('scale', in_color_matrix='bt601', out_color_matrix='bt709')

        if audio_file:
            audio = ffmpeg.input(audio_file).audio
            audio = audio.filter_('atrim', duration=video_duration)

        if audio is not None:
            stream = ffmpeg.concat(video, audio, v=1, a=1)
        else:
            stream = video

        try:
            cmd = (
                ffmpeg
                .compile(
                    stream
                    .output(output_file,
                            vcodec=codec,
                            crf=crf,
                            pix_fmt=pix_fmt,
                            colorspace=colorspace,
                            color_trc=colorspace,
                            color_primaries=colorspace,
                            movflags="faststart",
                            y=None)
                )
            )

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
