![PiGFM](md/pigfm.svg)

# PiGFM (Police, intelligence & Government Frequency Monitor)

![PiGFM](md/term_win.gif)

## PiGFM is open source software that turns an old laptop into an ICE / police early warning system for $30

### How PiGFM works
Police, ICE and other government agencies use two-way radio systems that are separate from the mobile phone system.
Their portable handheld radios and mobile vehicle radios transmit back to fixed base stations when the push-to-talk button is pressed. Luckily for us these radios are also typically configured to automatically send location and other information back to base regularly when they're switched on.

PiGFM works by detecting the signals these radios transmit. The 'strength' of these signals correspond roughly to how close the radio is and the software sounds an alarm if a set level is reached, alerting the user to an approaching radio.

### Who it's for
First they came for...

### It's not perfect!
Unfortunately the channels in these systems are typically shared with useful agencies (ambulance service, fire department etc) - PiGFM cannot currently tell the difference.
Some systems also have multiple blocks of channels spread apart in frequency - PiGFM cannot currently receive more than one 'block' of channels at the same time. See the improvements section for ideas about how PiGFM could be made more useful.

### What you'll need
- An old laptop or Raspberry Pi etc. Running Linux
- RTL-SDR compatible USB dongle - a cheap unit will work or you can get a higher quality unit from [RTL-SDR.COM](https://rtl-sdr.com)
- 10dB SMA attenuator (optional). To reduce help prevent overload of the RTL-SDR in high signal environments
- Small antenna with SMA connector

### How to install
In a terminal window run:

Update repositories `sudo apt update`

Install dependencies `sudo apt install libzmq3-dev python3-zmq python3-numpy gnuradio rtl-sdr gr-osmosdr`

PiGFM needs Python 3.10 or newer.

Download the program files as a ZIP file and decompress or clone this repository

Run directly `./pigfm.py config/<config_file>.ini` or `python3 -m pigfm config/<config_file>.ini`

Run `./pigfm.py --help` for the full list of options.

These instructions work under Ubuntu and Raspberry Pi OS.

### Edit the configuration file
PiGFM is very new and currently only has one configuration file included. This is for the Regional Mobile Radio network in Victoria, Australia.
Luckily for similar networks all you need to change is the 'centre_freq' section of the configuration file. Unfortunately, however, finding the information you'll need to determine the centre frequency parameter is a bit trickier. You'll need to find the lowest and highest frequencies of the channels you want to monitor. Sometimes the frequencies you'll find will be the base station transmit channels - the system will have a fixed frequency offset (typically 4.5MHz) between transmit and receive and you might have to take this into account when making the setting.

The information for channel allocations is typically publicly available, for example in Australia the [ACMA](https://www.acma.gov.au/register-radiocommunications-licences-rrl) and in the US the [FCC](https://www.fcc.gov/)

Once you've found your lowest and highest frequency the centre frequency can be calculated with:

`number_of_channels = ((highest_freq - lowest_freq) / channel_spacing) + 1`

`centre_freq = lowest_freq + ((number_of_channels / 2) - 0.5) x channel_spacing) + offset`

The channel spacing is typically 12.5kHz. If you've calculated the centre frequency of the base station transmit you'll have to add the offset to get the receive channels (typically 4.5MHz higher)

Web forums for radio scanner users may also be useful sources of information and help. There's also the Radio Resource website which has a page dedicated to RMR [https://www.radioreference.com/db/sid/7679](https://www.radioreference.com/db/sid/7679). If the system you're interested in has a page on this site it's a good start for gathering the necessary information.

Submissions of configuration files for other systems are very welcome.

### How to use
- The main window of the program displays a spectrum view of 256 channels centred on the centre frequency.
- The right-side window shows a log of which channels are currently active in red. Past detection events are shown in white and logged to a file.


The left-side control window displays settings and some status information:
- `⬆/⬇` keys modify the alarm threshold
- `m` key toggles the alarm mute
- `c` key clears the ignore list
- `a` key adds the most recent active channel to the ignore list
- `q` quits the program

Once everything is up and running you might see some channels active in the main window. The receiver is very sensitive and it's possible to pick up signals from a long way off even with a cheap antenna. Active transmitters that are closeby will produce large peaks on the main window - as a rough guide, signals will increase in level 6dB for every halving of the distance between the receiver and transmitter.

You might be able to take the receiver somewhere nearby a transmitter that you know is operating to get a feel for what kind of relationship there is between distance and the observed signal level.

### Checking your setup with the base stations
Portable and mobile radios only transmit when someone keys up or the radio
reports in, so a quiet band tells you nothing: you cannot tell a working setup
from a broken one. The base stations transmit far more often and with much more
power, so they are the quickest way to prove the receiver, the antenna and your
centre frequency are all right.

`./pigfm.py --base-station config/rmr.ini`

This tunes down by `duplex_offset` in the `[rf]` section of your config, 4.5MHz
by default, to listen to the base station downlink instead of the uplink. The
activity window title turns red and shows the offset so you cannot forget which
one you are looking at.

`--tuning-offset <Hz>` does the same thing with any shift you like, if your
system uses a different split or you just want to look somewhere else:

`./pigfm.py --tuning-offset -4600000 config/rmr.ini`

Both options are for testing and neither is saved. While an offset is applied
PiGFM will not write anything back to your config file at all, because channel
numbers refer to different frequencies then and an ignore list built while
listening to the base stations would suppress unrelated channels on your next
normal run.

### If PiGFM does not seem to hear anything
Run the self test first:

`./pigfm.py --self-test config/rmr.ini`

It tunes to the FM broadcast band, which is the loudest signal in the spectrum
almost anywhere, and reports how far above the noise it is at a range of gains.
That separates three things which otherwise look identical: a broken or
disconnected antenna, a gain that is too low, and a band that genuinely has
nothing on it.

Gain matters more than you might expect. On the machine this was developed
against, the shipped `gain = 20` heard FM broadcast 30dB above the noise, while
`gain = 40` heard it at 45dB and picked out fourteen times as many signals. If
the self test recommends a different gain, set it in the `[rf]` section or pass
`--gain`.

### Logging what transmits nearby
Identity needs P25. Noticing that *something* transmitted nearby, when, for how
long and how strongly, does not:

`./pigfm.py --watch --base-station config/rmr.ini`

```
18:16:20  ch 115 (166.6375 MHz)    8.6s  +16.4dB
18:16:56  ch 115 (166.6375 MHz)   38.2s  +13.6dB
18:17:27  ch  55 (165.8875 MHz)    4.2s  +13.6dB
```

Every transmission is printed as it ends and appended to `activity.log`, with a
periodic summary of the busiest channels. This works on any signal, digital or
analogue, so it is useful even where nothing P25 is in range.

Each channel is judged against its own recent noise floor rather than one
threshold across the band, because receiver response and local noise vary from
channel to channel. `--watch-margin` sets how far above its own floor a channel
has to rise; the default of 10dB is comfortably clear of noise without
discarding weak signals.

### Decoding who is transmitting
P25 systems send the radio ID and the talkgroup ID **unencrypted, even on
encrypted systems**. PiGFM can decode them.

First find a control channel. **A strong signal is very often not a P25 one**,
and you cannot tell by looking at a spectrum display. PiGFM measures it properly.

Any signal keyed at a symbol rate carries a spectral line at that rate, but where
that line appears depends on how the signal is keyed. P25 Phase 1 keys the
frequency, so its clock shows up in the instantaneous frequency. P25 Phase 2 keys
the phase, so there is nothing in the instantaneous frequency at all and its clock
shows up in the envelope instead. PiGFM tests for both, because each is completely
invisible to the other test:

| | Phase 1 C4FM | Phase 2 downlink | analogue FM | noise |
|---|---|---|---|---|
| C4FM column | 50x and up | ~2x | ~2x | ~1.5x |
| Phase2 column | ~1.4x | 60x and up | ~2x | ~1.5x |

`./pigfm.py --decode-scan --base-station config/rmr.ini`

This surveys the band, then tests the strongest channels and reports whether each
is really carrying P25 C4FM. Use `--base-station`, because the control channel is
transmitted continuously by the base station on the downlink.

Then decode it:

`./pigfm.py --decode --base-station --decode-channel 42 config/rmr.ini`

**Which way you are tuned decides what you can learn.** The wire format is the
same in both directions and nothing in a frame says which it is, so PiGFM takes
it from the tuning:

| tuned to | you hear | identities you get |
|---|---|---|
| the uplink (default) | radios transmitting | the radio that sent it, and it is **near you** |
| the downlink (`--base-station`) | the network transmitting | radios the network names, which may be anywhere |

For noticing radios close to the receiver, the uplink is the one that matters. A
radio has to announce itself to register, affiliate, request a channel or raise
an emergency, and hearing that transmission means the radio is within range.
Those messages are marked `RADIO NEARBY`; a radio merely named in a message from
the network is marked `radio named`.

The catch is that the control channel is the only place this traffic is
concentrated, and finding it is the hard part. Following whichever channel just
became active is too slow for a short registration burst: detection takes a sixth
of a second and retuning takes another half, by which time the burst is over. Use
`--decode-channel` to sit on the inbound control channel once you have found it.

Radios heard near the receiver are printed as they arrive, with the time, the
channel, how strong that radio was, and its ID, and appended to `sightings.log`:

```
* RADIO NEARBY  14:22:07  radio 5551234 talkgroup 1234  -6.2dB  ch 42 (170.2250 MHz)
```

There is also a periodic per channel summary that marks the control channel. Without `--decode-channel` the decoder follows
whichever channel most recently became active, which is useful for watching
traffic but will often land on a voice channel. Voice channels carry their
identities in link control, which PiGFM does not decode yet, so pin the control
channel if you want IDs.

How it works: the receiver already extracts one 12.5kHz channel as raw IQ. That
is demodulated as C4FM and clock recovered inside GNURadio, so what reaches the
Python side is 4800 symbols per second. From there PiGFM correlates the frame
sync, decodes the Network Identifier with a BCH(63,16) code to get the system NAC
and the frame type, and for trunking frames runs a Viterbi decoder over the rate
1/2 trellis code, checks the CRC, and reads the talkgroup and radio IDs out of
the result.

Every block is CRC checked, so identities that appear have passed a 16 bit check.

### Running without a receiver
You can run the whole program with no dongle attached:

`./pigfm.py --synthetic config/rmr.ini`

This fabricates a noise floor and channels keying up at the same frame rate the
real receiver produces, which is useful for trying PiGFM out, working on the
interface, or running the tests. Add `--seed 42` for a repeatable run.

### Development
The program is a package. The signal path is separated from the interface so
each piece can be tested on its own:

```
pigfm.py            entry point
pigfm/
  config.py         typed config, saves without destroying your comments
  radio.py          GNURadio flowgraph, spectrum branch and raw IQ branch
  frames.py         frame sources: the receiver, or synthetic traffic
  dsp/              noise floor levelling, channel power reduction
  scanner.py        channel activity detection, no interface code
  eventlog.py       event logging
  ui.py             curses windows
  app.py            wiring and the main loop
```

Run the tests with `python3 tests/run.py`, or with `pytest` if you have it.
`tests/reference.py` holds a verbatim copy of the original single-file version
of the signal path, and the tests assert the current code produces byte-identical
output for the same input. Any change to the DSP or the spectrum rendering that
alters behaviour will fail there, on purpose.

### Decoding
`radio.py` carries a second branch that extracts a single 12.5kHz channel as raw
IQ at 50kSPS and publishes it on port 5556. Nothing consumes it yet. It exists so
that channel metadata decoding (see the improvements section) can be built
without restructuring the receiver. Enable it with `--iq`, and choose the channel
with `--iq-channel`. It costs a little CPU, so it is off by default.

### More info
Although PiGFM was developed to monitor a digital (P25) trunked radio system it doesn't demodulate the channels - this means it will still detect signals in other trunked radio systems like TETRA in the EU and even older analogue systems.

PiGFM is a terminal program so it's lightweight and easy to install for users. The program consists of a GNURadio flowgraph that sets up the receiver, performs an FFT and filtering and then passes the framed data to a Python script. The script is responsible for UI, channel monitoring and alarm etc.

### Known frequencies for RMR
The Regional Mobile Radio network in Victoria is a P25 Phase II system on VHF,
with base station downlinks between **163.6 and 168.2 MHz**. Its site and control
channel frequencies are published in the
[RadioReference database](https://www.radioreference.com/db/sid/7679) and in the
[ACMA licence register](https://www.acma.gov.au/register-radiocommunications-licences-rrl),
which you can filter by trading name to see every site and its frequencies.

The shipped `config/rmr.ini` centres on the mobile uplink, so `--base-station`
puts you on the downlink where the control channel is. Note that the RMR band is
wider than one 3.2 MHz block: the shipped config with `--base-station` covers
165.2 to 168.4 MHz, so to sweep the lower part of the band as well you will need
`--tuning-offset -6893750`, which centres on 164.4 MHz.

### A note on frequency accuracy
Versions before 0.2.0 calculated channel frequencies half a channel low, which
put the displayed frequency scale one whole channel out and placed channel 0
outside the range the receiver can actually see. This is fixed. If you noted
channel numbers against frequencies using an older version, the frequencies were
one channel spacing (usually 12.5kHz) below the truth.

### Improvements
There are a lot of improvements that could be made to PiGFM!
- More config files for specific systems / locations.
- A prebuilt Raspberry Pi image or bootable USB drive with an operating system and PiGFM installed.
- 'Block' scanning - monitor several blocks of channels; this could be accomplished by retuning the receiver back and forth or with multiple receivers.
- ~~Decode metadata from the active channels~~ Done for P25 trunking frames, see 'Decoding who is transmitting' above. Still to do: link control on voice channels, so identities are recovered during a call as well as from the control channel. That needs Reed-Solomon and Hamming decoding on top of what is already there.
- Follow a channel grant onto its traffic channel, so a call can be tracked from the control channel to the voice channel carrying it.
- P25 Phase 2 is TDMA and does not use C4FM, so it needs a different demodulator.