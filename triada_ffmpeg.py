import sys
import re
import subprocess
import glob
from PyQt5.QtWidgets import QApplication, QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QFileDialog, QLabel, QComboBox, QLineEdit, QRadioButton, QSlider, QProgressBar
from PyQt5.QtCore import Qt, QThread, pyqtSignal
import ffmpeg


class EncoderThread(QThread):
    progress = pyqtSignal(int)

    def __init__(self, cmd):
        super().__init__()
        self.cmd = cmd

    def run(self):
        process = subprocess.Popen(self.cmd,
                                   stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT,
                                   universal_newlines=True)

        for line in process.stdout:
            print(line.strip())
            progress_match = re.search(r'frame=\s*(\d+)', line)

            if progress_match:
                frame_number = int(progress_match.group(1))
                self.progress.emit(frame_number)

        process.wait()


class FFmpegGUI(QWidget):
    def __init__(self):
        super().__init__()

        self.init_ui()

    def init_ui(self):
        self.setWindowTitle('Triada FFmpeg GUI')

        layout = QVBoxLayout()

        layout.addWidget(QLabel('Video or Image Sequence'))
        self.video_input = QLineEdit()
        self.video_button = QPushButton('Browse')
        self.video_button.clicked.connect(self.select_video)
        video_layout = QHBoxLayout()
        video_layout.addWidget(self.video_input)
        video_layout.addWidget(self.video_button)
        layout.addLayout(video_layout)

        layout.addWidget(QLabel('Alternative Audio Source (optional)'))
        self.audio_input = QLineEdit()
        self.audio_button = QPushButton('Browse')
        self.audio_button.clicked.connect(self.select_audio)
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

        self.encode_button = QPushButton('Encode')
        self.encode_button.clicked.connect(self.encode_video)
        layout.addWidget(self.encode_button)

        self.progress_bar = QProgressBar()
        layout.addWidget(self.progress_bar)

        self.setLayout(layout)

    def update_crf_label(self, value):
        self.crf_label.setText(f"Quality (CRF): {value}")

    def select_video(self):
        video_file, _ = QFileDialog.getOpenFileName()
        if video_file:
            self.video_input.setText(video_file)

    def select_audio(self):
        audio_file, _ = QFileDialog.getOpenFileName()
        if audio_file:
            self.audio_input.setText(audio_file)

    @staticmethod
    def get_frame_count(file_path):
        try:
            frame_count = 0

            # Check if the input is an image sequence
            if '%' in file_path:
                # Replace the %0xd part of the pattern with a wildcard
                pattern = re.sub(r'%0\d+d', '*', file_path)
                # Count the number of matching files in the directory
                frame_count = len(glob.glob(pattern))
            else:
                streams = ffmpeg.probe(file_path)["streams"]
                for stream in streams:
                    if stream['codec_type'] == 'video':
                        frame_count = int(stream['nb_frames'])

            return frame_count

        except (ffmpeg.Error, KeyError, StopIteration):
            return None

    def encode_video(self):
        video_file = self.video_input.text()
        audio_file = self.audio_input.text()
        crf = self.crf_slider.value()
        codec = self.codec_combo.currentText()
        pix_fmt = 'yuv420p' if self.pix_fmt_8bit.isChecked() else 'yuv420p10le'

        if not video_file:
            return

        output_file = f"output_{codec}_{pix_fmt}.mp4"

        if '%' in video_file:
            input_stream = ffmpeg.input(
                video_file, format='image2', framerate=25)
        else:
            input_stream = ffmpeg.input(video_file)

        try:
            if audio_file:
                audio_stream = ffmpeg.input(audio_file)
                audio = audio_stream.audio
                video = input_stream.video
                stream = ffmpeg.concat(video, audio, v=1, a=1)
            else:
                stream = input_stream

            frame_count = self.get_frame_count(video_file)
            if frame_count is not None:
                self.progress_bar.setMaximum(frame_count)

            cmd = (
                ffmpeg
                .compile(
                    stream
                    .output(output_file,
                            vcodec=codec,
                            crf=crf,
                            pix_fmt=pix_fmt,
                            y=None)
                )
            )

            print("FFmpeg command:", " ".join(cmd))

            self.encoder_thread = EncoderThread(cmd)
            self.encoder_thread.progress.connect(self.update_progress)
            self.encoder_thread.finished.connect(self.encoding_finished)
            self.encoder_thread.start()
            self.encode_button.setEnabled(False)

        except ffmpeg.Error as e:
            print(e.stderr.decode())

    def update_progress(self, frame_number):
        self.progress_bar.setValue(frame_number)

    def encoding_finished(self):
        self.progress_bar.reset()
        self.encode_button.setEnabled(True)
        print("Encoding finished")


if __name__ == '__main__':
    app = QApplication(sys.argv)
    window = FFmpegGUI()
    window.show()
    sys.exit(app.exec_())
