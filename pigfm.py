#!/usr/bin/env python3

### PiGFM by Sh4d0wm45k of The New Radicals ###

#                                                   ==============-                      -=======                                   
#                                             --=========================             -=========                                    
#                                         ===================================-     ============                                     
#                                      ========================================- =============                                      
#                                    =========================================================                                      
#                      ===         ============================================================                                     
#            ==      ========    -===============================================================                                   
#            ===    ====  ===   ==================================================================-                                 
#              ===--===   ===  ======================================================================                               
#                ===========  ==========================================================================                            
#                   ======   =====#@@@@@@@@@@*==@@@@#===+%@@@@@@@@#===@@@@@@@@@@@+#@@@@@@===*@@@@@@=============                    
#                    =============#@@@@@@@@@@@%=#@@@+==@@@@@@@@@@@@@==@@@@@@@@@@@=#@@@@@@+==@@@@@@@===============                  
#                     -===========#@@@@+==@@@@@=@@@@#=@@@@@+===+@%##==@@@@@=======#@@@@@@@=+@@@@@@@===============                  
#                        =-=======#@@@@@@@@@@@#=@@@@#=@@@@%==*@@@@@@#=@@@@@@@@@@*=#@@@#@@@=@@@%@@@@===============                  
#                           ======#@@@@@@@@@@*==@@@@#=@@@@%==#@@@@@@%=@@@@@@@@@@+=#@@@*@@@@@@@=@@@@===============                  
#                           ======#@@@@+========@@@@#=@@@@@+====@@@@%=@@@@@=======#@@@*=@@@@@@=@@@@===============                  
#                           ======#@@@@+========@@@@#==@@@@@@@@@@@@@%=@@@@@=======#@@@*=@@@@@==@@@@==============                   
#                           ======#@@@@+========@@@@#===*@@@@@@@@@@+==@@@@@=======#@@@*=+@@@@==@@@@=======                          
#                            ============================================================================                           
#                            ============ Police, intelligence & government frequency monitor ==========                            
#                             =========================================================================                             
#                              -=====================================================================                               
#                               ====================================================================                                
#                                ================================================================                                   
#                                 ============================================================                                      
#                                 =========================================================                                         
#                                  ========================================================                                         
#                                  ===========  =============-          ==================-                                         
#                                   ==========  =========               ==================-                                         
#                                    =========   ========               ===================                                         
#                                    =========   ========                ========= =======                                          
#                                     ========                           -========                                                  
#                                     ========                            -======-       

import sys
import zmq
import curses
import configparser
import numpy as np
from datetime import datetime
import time

import rtl_fft_source

class Event:
	def __init__(self, channel, pwr):
		self.start_time = time.time()
		self.start_datetime = datetime.now()
		self.channel = channel
		self.pwr = pwr
		self.max_pwr = pwr
		self.active = True

	def get_text(self):
		if self.active:
			return f'{self.start_datetime:%H:%M:%S}: Channel {self.channel} is active at {self.pwr:.1f}dB for {time.time() - self.start_time:.1f}s'

		else: return f'{self.start_datetime:%H:%M:%S}: Channel {self.channel} was active at {self.max_pwr:.1f}dB for {self.end_time - self.start_time:.1f}s'

	def set_inactive(self):
		self.active = False
		self.end_time = time.time()

	def update_power(self, pwr):
		self.pwr = pwr
		if pwr > self.max_pwr: self.max_pwr = pwr

class PigFm:
	def __init__(self, stdscr, config_file):
		self.stdscr = stdscr

		# Config from INI file
		self.config_file = config_file
		self.config = configparser.ConfigParser()
		self.config.read(self.config_file)

		self.centre_freq = self.config.getfloat('rf', 'centre_freq')
		self.channel_spacing = self.config.getfloat('rf', 'channel_spacing')
		self.rf_gain = self.config.getfloat('rf', 'gain')
		self.iir_alpha = self.config.getfloat('rf', 'iir_alpha')
		self.n_channels = self.config.getint('rf', 'n_channels')

		self.system_name = self.config.get('display', 'system_name')
		self.min_amp = self.config.getint('display', 'min_amp')
		self.max_amp = self.min_amp + self.config.getint('display', 'amp_range')
		self.amp_step = self.config.getint('display', 'amp_step')

		self.active_threshold = self.config.getint('scanner', 'active_threshold')

		self.alarm_mute = self.config.getboolean('alarm', 'mute')
		self.ignore_list = {int(x) for x in self.config.get('alarm', 'ignore_list', fallback = '').split(',') if x.strip()}

		self.do_log = self.config.getboolean('logging', 'do_log')
		logging_filename = self.config.get('logging', 'filename')

		ZMQ_ADDRESS = 'tcp://127.0.0.1:5555'

		#self.centre_freq -= 4500000	# For debugging listen to a local base station instead of portables. Check your RX / TX offset

		# RTL FFT source
		curses.def_prog_mode()	# Pause curses terminal mode while GNURadio does it's thing
		curses.endwin()

		self.rtl_source = rtl_fft_source.RtlFftSource(samp_rate = 3200000,
													  centre_freq = self.centre_freq,
													  rf_gain = self.rf_gain,
													  fft_size = 4096,
													  iir_alpha = self.iir_alpha,
													  keep_one_in_n = 128,
													  zmq_address = ZMQ_ADDRESS)

		self.rtl_source.start()

		curses.reset_prog_mode()
		self.stdscr.clear()

		# ZMQ receive
		self.context = zmq.Context()
		self.socket = self.context.socket(zmq.PULL)
		self.socket.connect(ZMQ_ADDRESS)

		# Noise floor levelling
		self.cal_cnt = 0
		self.noise_floor = np.zeros(self.n_channels)

		# Channel scanner
		self.events = [None] * 30

		# Logging
		if self.do_log: self.log_file = open(logging_filename, 'a')

		# Interface
		curses.curs_set(0)
		curses.mousemask(curses.ALL_MOUSE_EVENTS | curses.REPORT_MOUSE_POSITION)    # Stop mouse interfering
		curses.init_pair(1, curses.COLOR_RED, curses.COLOR_BLACK)
		curses.init_color(10, 1000, 412, 706)	# Pair 10 is hot pink
		curses.init_pair(2, 10, curses.COLOR_BLACK)

		self.SPEC_WIN_HEIGHT = 23
		self.SPEC_WIN_WIDTH = 136
		self.SCREEN_WIDTH = self.SPEC_WIN_WIDTH + 4
		self.spec_win_content = []
		self.CHARS = np.array(list(' ▖▌▌▗▄▙▙▐▟██▐▟██'))
		self.THRESHOLDS = np.arange(self.min_amp, self.max_amp, 2)

		self.ACTIVITY_WIN_MIN_HEIGHT = 9
		self.ACTIVITY_WIN_WIDTH = 64
		self.activity_rows = self.ACTIVITY_WIN_MIN_HEIGHT - 2

		self.CONTROL_WIN_HEIGHT = 9
		self.CONTROL_WIN_WIDTH = 64

		self.setup_windows()

		self.run()

	def channel_scanner(self, fft_frame):
		# Update active events
		active_events = [e for e in self.events if e and e.active]

		for event in active_events:
			pwr = fft_frame[event.channel]

			if pwr < self.active_threshold - 4:
				event.set_inactive()
				
				if self.do_log and event.channel not in self.ignore_list:
					self.log_event(f'{event.start_datetime:%Y-%m-%d} {event.get_text()}\n')

			else:
				event.update_power(pwr)

		# Add new events
		active_channels = set(np.where(fft_frame >= self.active_threshold)[0])
		active_events_channels = set([event.channel for event in active_events])
		newly_active_channels = active_channels - active_events_channels

		for channel in newly_active_channels:
			if channel not in self.ignore_list:
				self.events.insert(0, Event(channel = channel, pwr = fft_frame[channel]))
				self.events.pop()

				if not self.alarm_mute:
					curses.beep()

	def channel_to_freq(self, channel):
		return self.centre_freq + (channel - self.n_channels / 2) * self.channel_spacing - self.channel_spacing / 2

	def exit(self):
		self.rtl_source.close()
		self.config['scanner']['active_threshold'] = str(self.active_threshold)
		self.config['alarm']['mute'] = str(self.alarm_mute)
		self.config['alarm']['ignore_list'] = ', '.join(map(str, sorted(self.ignore_list)))

		with open(self.config_file, 'w') as f:
			self.config.write(f)

		self.log_file.close()

	def get_fft_frame(self):
		"""Blocks until a valid 4096 packet is received."""
		while True:
			try:
				raw_bytes = self.socket.recv()
				frame = np.frombuffer(raw_bytes, dtype = np.float32)
				if frame.size == 4096:
					# Reshape and find max in specific bins
					grid = frame.reshape(self.n_channels, 16)
					pwrs = np.max(grid[:, 4: 12], axis = 1)
					return pwrs

			except (ValueError, zmq.ZMQError):
				continue

	def format_fft_frame(self, fft_frame):
		"""Converts raw FFT numbers into block character strings."""
		pairs = fft_frame.reshape(128, 2) # Reshape into 128 pairs (column pairs)
		
		b0 = pairs[:, 0: 1] # Left bins
		b1 = pairs[:, 1: 2] # Right bins
		
		# Bitmasking for block character selection
		bits = (b0 > self.THRESHOLDS) * 1 | \
			   (b0 > self.THRESHOLDS + 1) * 2 | \
			   (b1 > self.THRESHOLDS) * 4 | \
			   (b1 > self.THRESHOLDS + 1) * 8
		
		grid = self.CHARS[bits].T[::-1] # Map to characters, transpose to 20x128, and flip vertically
		
		return [''.join(row) for row in grid]

	def level_noise_floor(self, fft_frame):
		if self.cal_cnt == 0:
			self.cal_cnt = 100  # Cal every frames

			alpha = 0.9
			noise_avg = fft_frame[0] # Moving average, starting at the first bin level

			for i in range(self.n_channels):
				if (fft_frame[i] - noise_avg) < 4:
					self.noise_floor[i] = fft_frame[i]
					noise_avg = noise_avg * (1 - alpha) + fft_frame[i] * alpha
				else:
					self.noise_floor[i] = noise_avg

				self.noise_floor[i] -= fft_frame[0]  # Normalise to the first bin

		self.cal_cnt -= 1

		return np.subtract(fft_frame, self.noise_floor)

	def log_event(self, text):
		self.log_file.write(text)
		self.log_file.flush()

	def refresh_all(self):
		if not self.main_win: return
		
		self.main_win.touchwin()	# Mark as dirty to force full redraw
		self.stdscr.noutrefresh()
		self.main_win.noutrefresh()
		if self.spec_win: self.spec_win.noutrefresh()
		if self.activity_win: self.activity_win.noutrefresh()
		curses.doupdate()

	def setup_windows(self):
		h, w = self.stdscr.getmaxyx()
		self.stdscr.clear()

		total_height_min = self.SPEC_WIN_HEIGHT + self.ACTIVITY_WIN_MIN_HEIGHT + 3
		main_w = min(self.SCREEN_WIDTH, w)
		self.main_win = curses.newwin(h, main_w, 0, 0)
		self.main_win.keypad(True)
		self.main_win.timeout(1)
		self.main_win.attron(curses.color_pair(2))
		self.main_win.box()
		self.main_win.attroff(curses.color_pair(2))
		main_win_title = '| PiGFM by The New Radicals |'
		self.main_win.addstr(0, (self.SCREEN_WIDTH - len(main_win_title)) // 2, main_win_title, curses.A_BOLD | curses.color_pair(2))

		# Only create sub windows if there is enough space
		if h >= total_height_min and w >= self.SCREEN_WIDTH:
			self.spec_win = self.main_win.derwin(self.SPEC_WIN_HEIGHT, self.SPEC_WIN_WIDTH, 1, 2)

			activity_win_height = h - 26
			self.activity_rows = activity_win_height - 2
			self.activity_win = self.main_win.derwin(activity_win_height, self.ACTIVITY_WIN_WIDTH, 25, 74)
			self.activity_win.box()
			title = self.system_name
			self.activity_win.addstr(0, (self.ACTIVITY_WIN_WIDTH - len(title)) // 2, title, curses.A_BOLD)

			self.control_win = self.main_win.derwin(self. CONTROL_WIN_HEIGHT, self.CONTROL_WIN_WIDTH, 25, 8)
			self.control_win.box()
			title = 'Control'
			self.control_win.addstr(0, (self.CONTROL_WIN_WIDTH - len(title)) // 2, title, curses.A_BOLD)
			self.update_control_window()

		else:
			self.main_win.addstr(1, 1, f'Resize terminal to at least {self.SCREEN_WIDTH} x {total_height_min}')

	def terminal_resize(self):
		# Delete old window objects to free memory
		if hasattr(self, 'spec_win'): del self.spec_win
		if hasattr(self, 'activity_win'): del self.activity_win
		del self.main_win
		
		curses.update_lines_cols()	# Update curses internal dimensions
		
		self.setup_windows()
		self.spec_win_content = []
		self.update_activity_window()
		self.update_control_window()

	def update_activity_window(self):
		if hasattr(self, 'activity_win'):
			events = [e for e in self.events if e is not None][: self.activity_rows]

			for i, event in enumerate(events):
					text_attr = curses.A_BOLD

					if event.active and event.channel not in self.ignore_list: text_attr |= curses.color_pair(1)

					text = event.get_text()

					try: self.activity_win.addstr(i + 1, 2, text + ' ' * (self.ACTIVITY_WIN_WIDTH - len(text) - 3), text_attr)

					except curses.error(): pass

					self.activity_win.refresh()

	def update_control_window(self):
		if hasattr(self, 'control_win'):
			row = 1
			col_1_x, col_2_x, col_3_x = 2, 10, 42
			self.control_win.addstr(row, col_1_x, 'Key', curses.A_BOLD)
			self.control_win.addstr(row, col_2_x, 'Action', curses.A_BOLD)
			self.control_win.addstr(row, col_3_x, 'Status', curses.A_BOLD)
			self.control_win.addstr(row + 2, col_1_x, '⬆⬇', curses.A_BOLD)
			self.control_win.addstr(row + 2, col_2_x, 'Alarm threshold', curses.A_BOLD)
			self.control_win.addstr(row + 2, col_3_x, f'{self.active_threshold}dB  ', curses.A_BOLD)
			self.control_win.addstr(row + 3, col_1_x, 'm', curses.A_BOLD)
			self.control_win.addstr(row + 3, col_2_x, 'Alarm mute', curses.A_BOLD)

			if self.alarm_mute: self.control_win.addstr(row + 3, col_3_x, 'Muted    ', curses.A_BOLD | curses.color_pair(1))
			else : self.control_win.addstr(row + 3, col_3_x, 'Not muted', curses.A_BOLD)

			self.control_win.addstr(row + 4, col_1_x, 'c', curses.A_BOLD)
			self.control_win.addstr(row + 4, col_2_x, 'Clear ignore list', curses.A_BOLD)

			max_len = 18
			text = ', '.join(map(str, sorted(self.ignore_list)))
			text = text[: max_len] + '...' if len(text) > max_len else text
			self.control_win.addstr(row + 4, col_3_x, f'{text:<{max_len + 3}}', curses.A_BOLD)

			self.control_win.addstr(row + 5, col_1_x, 'a', curses.A_BOLD)
			self.control_win.addstr(row + 5, col_2_x, 'Add channel to ignore list', curses.A_BOLD)
			self.control_win.addstr(row + 6, col_1_x, 'q', curses.A_BOLD)
			self.control_win.addstr(row + 6, col_2_x, 'Quit', curses.A_BOLD)

			self.control_win.refresh()

	def update_spectrum_window(self, fft_frame):
		if hasattr(self, 'spec_win'):
			new_spec_win_content = ['      ┌' + '─' * 128 + '┐']

			# Add amplitude scale and FFT data
			scale_map = {i * 5: f'{val:>3}dB ┤' for i, val in enumerate(range(self.max_amp, self.min_amp - self.amp_step, -self.amp_step))}

			for i, row in enumerate(fft_frame):
					new_spec_win_content.append(f'{scale_map.get(i, '      │')}{row}│')

			# Add frequency scale
			new_spec_win_content.append(f'{self.min_amp:>3}dB ┴────┬───────────────────┬───────────────────┬───────────────────┬───────────────────┬───────────────────┬───────────────────┬───┘')
			scale_freqs = [self.channel_to_freq(x) / 1e6 for x in range(8, 268, 40)]
			new_spec_win_content.append(f'      {scale_freqs[0]:.3f}MHz          {scale_freqs[1]:.3f}MHz          {scale_freqs[2]:.3f}MHz          ' \
										f'{scale_freqs[3]:.3f}MHz          {scale_freqs[4]:.3f}MHz          {scale_freqs[5]:.3f}MHz          {scale_freqs[6]:.3f}MHz')

			for i, row_str in enumerate(new_spec_win_content):
				try:
					if row_str == self.spec_win_content[i]:
						continue
				except IndexError:
					pass

				try:
					self.spec_win.addstr(i, 0, row_str)
				except curses.error:
					pass

			self.spec_win.refresh()
			self.spec_win_content = new_spec_win_content

	def run(self):
		while True:
			fft_frame = self.get_fft_frame()
			levelled_fft_frame = self.level_noise_floor(fft_frame)
			formatted_fft_frame = self.format_fft_frame(levelled_fft_frame)
			self.update_spectrum_window(formatted_fft_frame)

			self.channel_scanner(levelled_fft_frame)
			self.update_activity_window()

			try:
				key = self.main_win.getkey()
				valid_key = True

				if key == 'KEY_RESIZE': self.terminal_resize()
				elif key == 'm': self.alarm_mute = not self.alarm_mute
				elif key == 'c': self.ignore_list.clear()
				elif key == 'a': self.ignore_list.add(self.events[0].channel)
				elif key == 'KEY_UP' and self.active_threshold < self.max_amp: self.active_threshold += 1
				elif key == 'KEY_DOWN' and self.active_threshold > self.min_amp: self.active_threshold -= 1
				elif key == 'q': break
				else: valid_key = False

				if valid_key:
					self.update_control_window() 

			except:
				pass

		self.exit()

def main(stdscr, config_file):
	pigfm = PigFm(stdscr, config_file)

if __name__ == '__main__':
	if len(sys.argv) > 1:
		config_file = sys.argv[1]
		curses.wrapper(main, config_file)

	else:
		print(f'Usage: {sys.argv[0]} <config_file.ini>')

## You get what you give ##