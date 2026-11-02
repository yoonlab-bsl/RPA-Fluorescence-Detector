# RPA-Fluorescence-Detector
Contains code and fabrication files for the RPA device.

The files ads1x15.py, boot.py, and max31855.py are the libraries that should be uploaded to the Pico. It is up to the user whether to run the wired code or Wifi code. The wired code will need the device to be plugged into a computer running a program such as Thonny. The WiFi variant can be run by the standalone device, but needs to be renamed to main.py so that it automatically run on the Pico upon powering on. If many CSV files begin piling up in memory, it will need to be cleared manually by using a wired connection to a computer running Thonny. 

Notes on using the data analysis notebook are found under each code block in that notebook. 

A KiCad schematic is provided which details the connections and components used. 

Three svg files were used to laser-cut the acrylic panels (base, middle, and top).

A 3mf file contains all stl files used to make the device. The material used in the prototype was regular Polylite Black PLA on either a Prusa Mini (for most parts) or XL (for the two larger enclosure parts), with default parameters used in Prusa slicer. The f3z file is a Fusion-specific CAD model of the device that shows how the fabricated components are located.

A video of the fluorescence change and a video of the preparation and usage of the device are found here as well.
