#!/usr/bin/python3
import serial
import re
from time import sleep
import argparse
from datetime import datetime

import pyaudio
import wave
import threading
from threading import Timer
import time


import signal
import sys

signal.signal(signal.SIGINT, lambda sig, frame: sys.exit(0))

PORT='/dev/tty.usbserial-14240'
BAUDRATE=2400
TIMEOUT=0.1 # ?
STRFTIME='%Y-%m-%dT%H:%M:%S.%f' # ISO 8601 format

FILE_SAVE_DIRECTORY='data/'

buffer = []  # Initialize array to store audio frames

run = False
savingFile = False # indicates that a sound with level above a threshold has been detected and file is being saved
savingTimer = False # timer that will kick in when saving

SAVE_AUDIO = True
LEVEL_THRESHOLD = 80 # threshold above which it is considered a high sound
sample_format = pyaudio.paInt16  # 16 bits per sample
channels = 1 # 1 channel is probably enough
fs = 44100  # Record at 44100 samples per second
chunk = 1024  # Record in chunks of 1024 samples
audioDuration = 3 # for how long before and after the peak we want to record, in s
AUDIO_HW_ID = -1 # if 0 or bigger indicates the audio interface to be used


##########################################
def subbits(byte,mask,r_shift):
    return (byte & mask) >> r_shift

def is_maxhold(ctrl):
    maxhold_bits = subbits(ctrl,0b00110000,4)
    if maxhold_bits == 0b10:
        return True
    elif maxhold_bits == 0b01:
        return False
    return None

def modetxt(ctrl):
    if subbits(ctrl,0b00001100,2) == 0b10:  # Leq mode
        slowmode = subbits(ctrl,0b00000010,1) == 0b1
        basedon_minutes = subbits(ctrl,0b00000001,0) == 0b1
        Leq_mode = True
    else:
        slowmode = subbits(ctrl,0b00000001,0) == 0b1
        basedon_minutes = None
        Leq_mode = False

    non_Leq_modes={
            0b000: 'Lp_(dB),Weighting_A',
            0b001: 'Lp_(dB),Weighting_C',
            0b010: 'Lp_(dB),Flat',
            0b011: 'Ln_(%),Weighting_A',
            0b101: 'Unknown',
            0b110: 'Cal_(dB)'
            }
    if Leq_mode:
        txt = 'Leq_(dB),Weighting_A'
        if basedon_minutes:
            txt+=',based_on_minutes'
        else:
            txt+=',based_on_10s'
    else:
        txt = non_Leq_modes[subbits(ctrl,0b00001110,1)]

    if slowmode:
        txt+=',Slow'
    else:
        txt+=',Fast'

    if is_maxhold(ctrl):
        txt+=',MaxHold'
    return txt

def chkchksum(msg):
    if len(msg)<=2: return False
    return int(msg[-1]) == (sum(x for x in msg[:-1]) % 256)

def decode_msg(msg):
    m = re.match(b'^\x08\x04(?P<ctrl>.)\x0a\x0a(?P<value>...)\x01$', msg[:-1])
    if not m:
        return None

    d=m.groupdict()
    try:
        val="%0.1f" % (d['value'][0]*10+d['value'][1]+d['value'][2]/10)
    except:
        val=None

    return (val,modetxt(ord(d['ctrl'])))

def trySerialOpen(port, maxTries):
    if (maxTries <=0):
        print('Port cannot be opened, giving up')
        return -1
    try:
        port.open()
    except:
        print('Could not open the serial port, retrying in 5 seconds...')
        sleep(5)
        trySerialOpen(port, maxTries-1)

def sensorThread():
    global run
    global savingFile
    global savingTimer
    global audioDuration
    global FILE_SAVE_DIRECTORY

    logFilename = 'log_' + datetime.now().strftime('%Y-%m-%dT%H-%M-%S') + '.txt'
    csvFile = open(FILE_SAVE_DIRECTORY + logFilename, "a")

    ser = serial.Serial()
    ser.baudrate = BAUDRATE
    ser.port = PORT
    ser.timeout = TIMEOUT

    trySerialOpen(ser, 100)
    print("Serial port opened " + str(PORT))
    while run:
        ### wait for heartbeat from device
        char = ser.read()
        if char==b'\x10': # heartbeat
            #print("hb received")
            ser.write(b'\x20')
        elif char==b'': continue
        else: # out of sync?
            sleep(1)
            continue
        #print(char)

        ### read message
        msg=bytes()
        while True:
            char = ser.read()
            if char==b'':
                #print("timeout")
                break
            msg+=char

        ### check chksum
        if len(msg)<1:
            print("# no message received")
            continue

        if not chkchksum(msg):
            print("# chksum error, msg: "+str(msg))
            #print("# chksum error")
            continue

        ### decode
        dt=datetime.now().strftime(STRFTIME)

        val,msg = decode_msg(msg)        
        csvLine = dt + ',' + val + ',' + msg
        print (csvLine)
        csvFile.write(csvLine + '\n')
        csvFile.flush()

        if SAVE_AUDIO and val and float(val) > LEVEL_THRESHOLD:
                print("LOUD SOUND!")
                if savingTimer or savingFile:
                    # defer timer for later
                    savingTimer.cancel()
                    savingTimer = Timer(audioDuration, audioFileSaveThread)
                    savingTimer.start()
                else:
                    # launch a saving thread in a few seconds
                    savingTimer = Timer(audioDuration, audioFileSaveThread)
                    savingTimer.start()

    # end of run loop
    csvFile.close()

def audioRecordThread():
    global run
    global stream
    global buffer
    global fs
    global savingFile
    global audioDuration
    while run:
        # read the audio data and place it in a buffer
        audiodata = stream.read(chunk)
        buffer.append(audiodata)
        
        # discard data older than a few secs
        totalSamples = (len(buffer) * 1024)
        if (totalSamples > (audioDuration * fs)) and not savingFile and not savingTimer:
            oldChunks =  len(buffer) - round((audioDuration * fs) / 1024)
            del buffer[:oldChunks]



def audioFileSaveThread():
    global savingFile
    global savingTimer
    global buffer
    global FILE_SAVE_DIRECTORY
    if not savingFile:
        print("launching audio file saving thread")
        savingFile = True
        filename = 'audio_' + datetime.now().strftime('%Y-%m-%dT%H-%M-%S') + '.wav'
        fileBuffer = buffer.copy()
        # Save the recorded data as a WAV file
        wf = wave.open(FILE_SAVE_DIRECTORY + filename, 'wb')
        wf.setnchannels(channels)
        wf.setsampwidth(portAudio.get_sample_size(sample_format))
        wf.setframerate(fs)
        wf.writeframes(b''.join(fileBuffer))
        wf.close()
        savingFile = False
        savingTimer = False
        print("audio file saved: " + FILE_SAVE_DIRECTORY + filename)


##########################################


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Sauter SU logger')
    parser.add_argument('-d', '--device', required=False, default=PORT, help='serial device (default: '+PORT+')')
    parser.add_argument('-f', '--datafolder', required=False, default=FILE_SAVE_DIRECTORY, help='folder where data is saved (default: ' +FILE_SAVE_DIRECTORY+ ')')
    parser.add_argument('-s', '--saveaudio', required=False, default=SAVE_AUDIO, help='save audio when above threshold (default: true)')
    parser.add_argument('-l', '--levelthreshold', required=False, default=LEVEL_THRESHOLD, help='save audio when above threshold (default: 80db)')
    parser.add_argument('-i', '--audiohwid', required=False, default=AUDIO_HW_ID, help='ID of the audio interface')


    args=parser.parse_args()

    if SAVE_AUDIO:
        portAudio = pyaudio.PyAudio()  # Create an interface to PortAudio

        if AUDIO_HW_ID == -1:
            stream = portAudio.open(format=sample_format,
                        channels=channels,
                        rate=fs,
                        frames_per_buffer=chunk,
                        input=True)
        else:
            print('Opening audio interface ' + AUDIO_HW_ID)
            portAudio.get_device_info_by_index(AUDIO_HW_ID)
            stream = portAudio.open(format=sample_format,
                        channels=channels,
                        rate=fs,
                        frames_per_buffer=chunk,
                        input=True,
                        input_device_index=AUDIO_HW_ID)
        

    run = True
    if SAVE_AUDIO:
        rt = threading.Thread(target=audioRecordThread)
        rt.start()
    st = threading.Thread(target=sensorThread)
    st.start()

    print("Starting data collection")

    # time.sleep(40)

    # run = False

    if SAVE_AUDIO:
        rt.join()
    st.join()

    print("Data collection stopped")

    if SAVE_AUDIO:
        # Stop and close the audio stream 
        stream.stop_stream()
        stream.close()
        # Terminate the PortAudio interface
        portAudio.terminate()
    


    