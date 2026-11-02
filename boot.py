from machine import Pin

for pin in range(29):
    Pin(pin, Pin.IN)