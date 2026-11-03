# ── EXPERIMENT ──────────────────────────────────────────────────
experiment_minutes = 20
# ── LED EXCITATION ──────────────────────────────────────────────
sampling_period_s = 10
warmup_period_s = 2
# ── HEATER CONTROL ─────────────────────────────────────────────
target_temp       = 39.0
warmup_overshoot  = 0.5
heater_bias       = 0.15
kp                = 1.0
# ── RUNNING-MODE DUTY CLAMP ────────────────────────────────────
running_duty_min  = 0.00
running_duty_max  = 0.25
# ─────────────────────────────────────────────────────────────────
import sys, select, _thread, gc
from machine import I2C, Pin, PWM, RTC, SPI
from ads1x15 import ADS1115
from max31855 import MAX31855
import network
import socket
import os
import utime

# ── SHARED STATE – Core 0 writes, Core 1 reads ─────────────────
shared_temp     = [None]
shared_running  = [False]
shared_start_ms = [0]
shared_done_msg = [""]
file_busy       = [False]

# ── CORE 1 – Wi-Fi AP + Web Server ─────────────────────────────
def wifi_thread():
    ap = network.WLAN(network.AP_IF)
    ap.active(True)
    ap.config(essid="RPA Data", password="12345678")
    for _ in range(20):
        if ap.active():
            break
        utime.sleep_ms(250)
    print("[WiFi] AP active — http://192.168.4.1")

    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", 80))
    srv.listen(2)
    srv.settimeout(0.5)

    while True:
        cl = None
        try:
            try:
                cl, _ = srv.accept()
                cl.settimeout(2)
            except OSError:
                utime.sleep_ms(200)
                continue

            req = cl.recv(1024).decode()
            parts = req.split(" ")
            if len(parts) < 2:
                continue
            path = parts[1]

            if path == "/":
                t = shared_temp[0]
                temp_str = f"{t:.2f} C" if t is not None else "-- C"
                is_running = shared_running[0]
                done_msg = shared_done_msg[0]

                if is_running:
                    elapsed_s = utime.ticks_diff(utime.ticks_ms(), shared_start_ms[0]) // 1000
                    elapsed_m, elapsed_sec = divmod(elapsed_s, 60)
                    elapsed_str = f"{elapsed_m}:{elapsed_sec:02d}"
                    status_html = (
                        '<p style="color:green;font-weight:bold;">'
                        'Running ' + elapsed_str + ' elapsed</p>'
                    )
                    refresh = '<meta http-equiv="refresh" content="1">'
                elif done_msg:
                    status_html = (
                        '<p style="color:blue;font-weight:bold;">'
                        'Complete ' + done_msg + '</p>'
                    )
                    refresh = ''
                else:
                    status_html = '<p style="color:gray;">Idle (not running)</p>'
                    refresh = '<meta http-equiv="refresh" content="5">'

                files = ""
                for f in sorted(os.listdir("/")):
                    if f.endswith(".csv"):
                        files += '<p><a href="/' + f + '">' + f + '</a></p>\n'
                if not files:
                    files = "<p>No CSV files</p>"

                html = (
                    "<html><head>" + refresh + "</head><body>"
                    "<h2>Temp: " + temp_str + "</h2>"
                    + status_html +
                    "<h3>CSV Files</h3>" + files +
                    "</body></html>"
                )
                cl.send("HTTP/1.1 200 OK\r\nContent-Type: text/html\r\n\r\n" + html)

            elif path.endswith(".csv"):
                fname = path[1:]
                if file_busy[0]:
                    cl.send(
                        "HTTP/1.1 503 Service Unavailable\r\n"
                        "Retry-After: 15\r\n\r\n"
                        "File busy — experiment in progress. Try again in a few seconds."
                    )
                else:
                    file_busy[0] = True
                    try:
                        size = os.stat(fname)[6]
                        cl.send(
                            "HTTP/1.1 200 OK\r\n"
                            "Content-Type: application/octet-stream\r\n"
                            'Content-Disposition: attachment; filename="' + fname + '"\r\n'
                            "Content-Length: " + str(size) + "\r\n\r\n"
                        )
                        with open(fname, "rb") as f:
                            while True:
                                chunk = f.read(1024)
                                if not chunk:
                                    break
                                cl.send(chunk)
                    except:
                        cl.send("HTTP/1.1 404 Not Found\r\n\r\nFile not found")
                    finally:
                        file_busy[0] = False
            else:
                cl.send("HTTP/1.1 404 Not Found\r\n\r\nNot found")

        except OSError:
            pass
        except Exception as e:
            print("[WiFi] Error:", e)
        finally:
            if cl:
                try:
                    cl.close()
                except:
                    pass
            file_busy[0] = False
            gc.collect()

# ── HELPER FUNCTIONS ───────────────────────────────────────────
def key_pressed(): return select.select([sys.stdin], [], [], 0)[0]
def fmt_time(s): return f"{s//60}:{s%60:02d}"
def _b(z, f, ms): z.freq(f); z.duty_u16(32768); utime.sleep_ms(ms); z.duty_u16(0)

def beep_melody(z):
    for f in (1200, 1500, 1800, 2000): _b(z, f, 80); utime.sleep_ms(40)

def lid_beep(z):
    _b(z, 1000, 100); utime.sleep_ms(50)
    _b(z, 1400, 100); utime.sleep_ms(50)
    _b(z, 1000, 100)

def debounce(pin, prev, last_ms, now):
    val = pin.value()
    if val != prev and utime.ticks_diff(now, last_ms) >= 300:
        return val, val, now
    return val, prev, last_ms

# ── FILENAME GENERATION ────────────────────────────────────────
_RTC_EPOCH_YEAR = 2024

def _next_sequential_filename():
    import uos
    existing = set()
    try:
        for entry in uos.ilistdir("/"):
            name = entry[0]
            if name.startswith("experiment_") and name.endswith(".csv"):
                try:
                    n = int(name[len("experiment_"):-len(".csv")])
                    existing.add(n)
                except ValueError:
                    pass
    except OSError:
        pass
    n = 1
    while n in existing:
        n += 1
    return f"experiment_{n:03d}.csv"

def make_csv_filename(rtc):
    try:
        y, mo, d, _, h, mi, *_ = rtc.datetime()
        if y >= _RTC_EPOCH_YEAR and 1 <= mo <= 12 and 1 <= d <= 31:
            return f"{h%12 or 12:02d}{mi:02d}{'AM' if h<12 else 'PM'}_{mo:02d}{d:02d}{y}.csv"
    except Exception:
        pass
    name = _next_sequential_filename()
    print(f"RTC unavailable or reset — using sequential filename: {name}")
    return name

# ── MAIN ───────────────────────────────────────────────────────
def main():
    # ── START WI-FI ON CORE 1 ──────────────────────────────────
    utime.sleep_ms(1000)
    _thread.start_new_thread(wifi_thread, ())
    utime.sleep_ms(1000)

    # ── BUZZER INIT & BOOT BEEP ────────────────────────────────
    buz = PWM(Pin(13)); buz.duty_u16(0)
    _b(buz, 1500, 100)

    # ── HARDWARE INIT ──────────────────────────────────────────
    i2c = I2C(0, sda=Pin(16), scl=Pin(17), freq=400_000)
    utime.sleep_ms(100)
    if 0x48 not in i2c.scan(): print("Error: ADC not found"); return
    adc = ADS1115(i2c, address=0x48, gain=2)

    spi = SPI(1, baudrate=5_000_000, polarity=0, phase=0,
              sck=Pin(10), mosi=Pin(11), miso=Pin(8))
    tc = MAX31855(spi, cs_pin=9)

    heater = Pin(15, Pin.OUT); heater.off()
    led = Pin(14, Pin.OUT); led.off()
    rtc = RTC()

    # ── LED EXCITATION TIMING ──────────────────────────────────
    led_on_ms        = int(sampling_period_s * 0.20 * 1000)
    warmup_led_on_ms = int(warmup_period_s * 0.20 * 1000)

    # ── LID SETUP ──────────────────────────────────────────────
    lid_btn = Pin(0, Pin.IN, Pin.PULL_UP)
    lid_prev = lid_btn.value()
    last_lid_ms = 0

    # ── STATE VARIABLES ────────────────────────────────────────
    running = finishing = False
    target_reached = False
    tc_fault = False
    last_fault_beep = 0
    adc_fault = False
    last_adc_fault_beep = 0
    csv_filename = ""
    current_temp = None
    duty_percent = 0.0
    heater_on_ms = 0
    burst_logged = False
    burst_sums, burst_count, exp_cycle_index = [0]*4, 0, 0
    first_burst_pending = True
    temp_stable_since = 0
    run_start_ms = 0

    # ── LID STATE ──────────────────────────────────────────────
    lid_open = lid_prev == 1
    lid_opened_at = utime.ticks_ms() if lid_open else 0
    lid_beeping = False
    last_lid_beep = 0
    last_closed_mv = None
    lid_was_opened = False
    lid_closed_at = utime.ticks_ms() - 5000

    now = utime.ticks_ms()
    last_tc_t = heater_win_start = cycle_start = last_idle = now

    print("Close lid to warm up, load samples, close lid to start")

    while True:
        now = utime.ticks_ms()

        # ── DEBOUNCED INPUTS ───────────────────────────────────
        _, lid_stable, last_lid_ms = debounce(lid_btn, lid_prev, last_lid_ms, now)
        lid_rose = lid_stable == 1 and lid_prev == 0
        lid_fell = lid_stable == 0 and lid_prev == 1
        lid_prev = lid_stable

        # ── LID TRANSITIONS ───────────────────────────────────
        if lid_rose:
            lid_open = True
            lid_opened_at = now
            lid_beeping = False
            lid_was_opened = True
            if not running:
                print("Lid opened")
            else:
                lid_beeping = True
                last_lid_beep = 0
                print("WARNING: Lid open during run")
        elif lid_fell:
            lid_open = False
            lid_beeping = False
            lid_closed_at = now
            print("Lid closed")

        if not running and lid_open and utime.ticks_diff(now, lid_opened_at) >= 10000:
            lid_beeping = True
        if lid_beeping and utime.ticks_diff(now, last_lid_beep) >= 2000:
            last_lid_beep = now
            lid_beep(buz)

        # ── LID HEATER CLAMP ──────────────────────────────────
        lid_clamp = (lid_open or utime.ticks_diff(now, lid_closed_at) < 5000)

        # ── CYCLE TIMING ──────────────────────────────────────
        active_period_ms = sampling_period_s * 1000 if running else warmup_period_s * 1000
        active_led_on_ms = led_on_ms if running else warmup_led_on_ms
        elapsed_cycle = utime.ticks_diff(now, cycle_start)

        if elapsed_cycle >= active_period_ms:
            cycle_start = now; elapsed_cycle = 0
            burst_logged = False; burst_sums = [0]*4; burst_count = 0

        led_on = elapsed_cycle < active_led_on_ms

        # ── DATA ACQUISITION ──────────────────────────────────
        if led_on and not burst_logged:
            try:
                readings = [adc.read(7, ch) for ch in range(4)]
                for ch in range(4):
                    burst_sums[ch] += readings[ch]
                burst_count += 1
                adc_fault = False
            except:
                if not adc_fault:
                    adc_fault = True
                    print("ADC FAULT")

        elif not led_on and not burst_logged and burst_count > 0:
            burst_logged = True
            mvolts = [adc.raw_to_v(burst_sums[ch]/burst_count)*1000 for ch in range(4)]
            max_mv = max(mvolts)

            if running:
                cycle_num = exp_cycle_index
                exp_cycle_index += 1
                if first_burst_pending:
                    first_burst_pending = False
                else:
                    label = fmt_time(cycle_num * sampling_period_s)
                    temp_str = f"{current_temp:.2f}" if current_temp is not None else "NaN"
                    duty_str = f"{duty_percent:.1f}"
                    mv = [f"{v:.1f}" for v in mvolts]

                    # ── GUARDED CSV WRITE ──────────────────────
                    if file_busy[0]:
                        print(f"[{label}] skipped write — file busy (WiFi transfer)")
                    else:
                        file_busy[0] = True
                        try:
                            with open(csv_filename, "a") as f:
                                f.write(f"{label},{','.join(mv)},{temp_str},{duty_str}\n")
                        except Exception as e:
                            print(f"[{label}] write error: {e}")
                        finally:
                            file_busy[0] = False

                    print(f"[{label}] A0={mv[0]} A1={mv[1]} A2={mv[2]} A3={mv[3]}mV T={temp_str}°C duty={int(duty_percent)}% n={burst_count}")

                if exp_cycle_index > (experiment_minutes * 60) // sampling_period_s:
                    finishing = True

            else:
                if not lid_open:
                    if lid_was_opened and last_closed_mv is not None and max_mv - last_closed_mv >= 100:
                        lid_was_opened = False
                        print(f"Auto-start: {max_mv:.1f}mV vs baseline {last_closed_mv:.1f}mV")
                        csv_filename = make_csv_filename(rtc)
                        with open(csv_filename, "w") as f: f.write("time,A0,A1,A2,A3,Temp,Duty\n")
                        beep_melody(buz); cycle_start = utime.ticks_ms()
                        exp_cycle_index = 0; burst_logged = False; burst_sums = [0]*4; burst_count = 0
                        first_burst_pending = True
                        running = True
                        run_start_ms = utime.ticks_ms()
                        shared_running[0] = True
                        shared_start_ms[0] = run_start_ms
                        shared_done_msg[0] = ""
                        print(f"Started {csv_filename}")
                    else:
                        lid_was_opened = False
                        last_closed_mv = max_mv

        # ── EXPERIMENT FINISH ─────────────────────────────────
        if finishing:
            heater.off(); led.off()
            shared_running[0] = False
            elapsed_s = utime.ticks_diff(utime.ticks_ms(), run_start_ms) // 1000
            elapsed_m, elapsed_sec = divmod(elapsed_s, 60)
            shared_done_msg[0] = f"{elapsed_m}:{elapsed_sec:02d}"
            utime.sleep_ms(50)
            print(f"Done. CSV: {csv_filename}")
            beep_melody(buz)
            return

        # ── LED OUTPUT ────────────────────────────────────────
        led.value(1 if led_on else 0)

        # ── HEATER OUTPUT ─────────────────────────────────────
        heater.value((not running or not led_on)
                     and utime.ticks_diff(now, heater_win_start) < heater_on_ms)

        # ── TEMPERATURE READING ───────────────────────────────
        if utime.ticks_diff(now, last_tc_t) >= 250:
            last_tc_t = now
            try:
                t = tc.read_celsius()
            except Exception as e:
                print(f"TC read error: {e}")
                t = None

            if t is None or t == 0:
                if not tc_fault:
                    tc_fault = True
                    print("TC FAULT")
            else:
                tc_fault = False
                current_temp = t
                shared_temp[0] = t

                # ── STABLE TEMP CHECK ─────────────────────────
                warmup_target = target_temp + warmup_overshoot
                if not running and not target_reached and current_temp >= warmup_target:
                    if temp_stable_since == 0:
                        temp_stable_since = now
                    elif utime.ticks_diff(now, temp_stable_since) >= 5000:
                        target_reached = True
                        _b(buz, 2000, 150)
                        print(f"Target {warmup_target}°C reached (stable)")
                else:
                    temp_stable_since = 0

        # ── TC FAULT BEEP ─────────────────────────────────────
        if tc_fault and utime.ticks_diff(now, last_fault_beep) >= 3000:
            last_fault_beep = now
            for _ in range(3): _b(buz, 1800, 80); utime.sleep_ms(80)

        # ── ADC FAULT BEEP ────────────────────────────────────
        if adc_fault and utime.ticks_diff(now, last_adc_fault_beep) >= 3000:
            last_adc_fault_beep = now
            for _ in range(3): _b(buz, 1800, 80); utime.sleep_ms(80)

        # ── HEATER DUTY CALCULATION (P CONTROLLER) ────────────
        if utime.ticks_diff(now, heater_win_start) >= 250:
            heater_win_start = now
            if current_temp is None or lid_clamp:
                duty = heater_bias
            else:
                effective_target = target_temp + warmup_overshoot if not running else target_temp
                error = effective_target - current_temp
                duty = heater_bias + kp * error
                if running:
                    duty = max(running_duty_min, min(running_duty_max, duty))
                else:
                    duty = max(0.0, min(1.0, duty))
            heater_on_ms = int(duty * 250)
            duty_percent = duty * 100

        # ── IDLE STATUS PRINTING ──────────────────────────────
        if not running and utime.ticks_diff(now, last_idle) >= 2000:
            last_idle = now
            td = f"{current_temp:.2f}°C" if current_temp is not None else "--°C"
            bl = f" baseline={last_closed_mv:.1f}mV" if last_closed_mv is not None else ""
            print(f"T={td} duty={int(duty_percent)}%{bl}")

        # ── INPUT HANDLING ────────────────────────────────────
        if key_pressed():
            key_char = sys.stdin.read(1).lower()
            if key_char == 's' and not running:
                csv_filename = make_csv_filename(rtc)
                with open(csv_filename, "w") as f: f.write("time,A0,A1,A2,A3,Temp,Duty\n")
                beep_melody(buz); cycle_start = utime.ticks_ms()
                exp_cycle_index = 0; burst_logged = False; burst_sums = [0]*4; burst_count = 0
                first_burst_pending = True
                running = True
                run_start_ms = utime.ticks_ms()
                shared_running[0] = True
                shared_start_ms[0] = run_start_ms
                shared_done_msg[0] = ""
                print(f"Started {csv_filename}")

        utime.sleep_ms(5)

try: main()
except KeyboardInterrupt: pass
finally:
    Pin(15, Pin.OUT).off()
    Pin(14, Pin.OUT).off()
    PWM(Pin(13)).deinit()
