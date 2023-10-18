import phue
import logging
from time import sleep
import struct
import json
import urllib
import ssl
from datetime import timedelta, datetime
import dateutil.parser
from skyfield import almanac, api
from pathlib import Path


### parameters
ipaddressSENEC = "192.168.178.3"
ipaddressHUE   = "192.168.178.4"
powerplug1 = ["Hue smart plug 1", 600, False]  # !!!sort by powerconsumption!!!
powerplug2 = ["Hue smart plug 2", 1950, False] # in Watts, of the activated device. Used to subtract this energy, if activated. False means: off
maxchargingpower = 1150  # in Watts. Maximum charging power of Batt. Used to determine, wether to switch off powerplugs if on and battery still too empty
location = api.Topos('49.533 N', '8.6433 E', elevation_m= 140)


# debug = False
# writepath = Path('home/sbrems/Desktop/senec/')
## SSL Setup for SENEC https
script_dir = Path( __file__ ).parent
# provied SSL file you get from my-senec.com (called SenecGui_root). If no valid path provided, SSL will be bypassed
pcertfile = script_dir.joinpath('SenecGui-Root.pem')
ctx = ssl.create_default_context()
# ctx.check_hostname = False

logging.basicConfig(filename=script_dir.joinpath('SENEC_HUE.log'), encoding='utf-8', level=logging.INFO)

if pcertfile.exists():
    logging.info('Using SSL certificate file %s for https connection to SENEC speicher.', pcertfile)
    ctx.load_verify_locations(pcertfile)
else:
    logging.warning('Warning. Not using SSL certificate file. This might be a security issue. Check if file %s exists',
           pcertfile)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

# get ephem data
ts = api.load.timescale()
ephem = api.load_file(script_dir / 'de413.bsp')
bridge = phue.Bridge(ipaddressHUE)

sunriseset = {}


# switch off at beginning
bridge.set_light(powerplug1[0], 'on', False)
bridge.set_light(powerplug2[0], 'on', False)
sleep(3)

def compute_sunrise_sunset(location, year=datetime.now().year,
                                     month=datetime.now().month,
                                     day=datetime.now().day):
    t0 = ts.utc(year, month, day, 0)
    # t1 = t0 plus one day
    t1 = ts.utc(t0.utc_datetime() + timedelta(days=1))
    if t0 in sunriseset.keys():
        sunrise, sunset = sunriseset[t0]
    else:
        t, y = almanac.find_discrete(t0, t1, almanac.sunrise_sunset(ephem, location))
        sunrise = None
        for time, is_sunrise in zip(t, y):
            if is_sunrise:
                sunrise = time
            else:
                sunset = time
        sunriseset[t0] = [sunrise, sunset]
    return sunrise, sunset

#ipaddress = str(sys.argv[1])


def get_current_power2grid_power2bat_housepower_batpercent():
    reqdata='{"ENERGY":{"GUI_GRID_POW":"", "GUI_BAT_DATA_POWER":"", "GUI_HOUSE_POW":"", "GUI_BAT_DATA_FUEL_CHARGE":""}}'
    response = urllib.request.urlopen('https://'+ ipaddressSENEC +'/lala.cgi' , data=reqdata.encode('utf-8'), context=ctx)
    jsondata = json.load(response)
    gridpower = myDecode(jsondata['ENERGY']['GUI_GRID_POW'])
    batpower = myDecode(jsondata['ENERGY']['GUI_BAT_DATA_POWER'])
    housepower = myDecode(jsondata['ENERGY']['GUI_HOUSE_POW'])
    batpercent = myDecode(jsondata['ENERGY']['GUI_BAT_DATA_FUEL_CHARGE'])

    return [-gridpower, batpower, housepower, batpercent]

def is_daytime(return_hours_left=False):
    # check if it is daytime. Return True/False
    sunrise, sunset = compute_sunrise_sunset(location)
    nowtimeint = strtime2seconds(datetime.utcnow().ctime()[11:19].replace(":",""))
    sunrisetimeint = strtime2seconds(sunrise.tt_strftime()[11:19].replace(":",""))
    sunsettimeint = strtime2seconds(sunset.tt_strftime()[11:19].replace(":",""))

    logging.debug('now: %s, sunrise: %s, sunset: %s', nowtimeint/3600, sunrisetimeint/3600, sunsettimeint/3600)
    # daytime
    if (nowtimeint > sunrisetimeint) and (nowtimeint < sunsettimeint):
        if return_hours_left:
            hours_left = (sunsettimeint - nowtimeint)/3600
            return [True, hours_left]
        else:
            return True
    # nighttime
    else:
        if return_hours_left:
            hours_left = (sunrisetimeint - nowtimeint)/3600
            # use beforetimesunrise as proxy for sunrisetime tomorrow. might be off by up to 2 min
            if hours_left < 0:
                 hours_left += 24
            return [False, hours_left]
        else:
            return False


def strtime2seconds(strtime):
    # convert timestring of hour, min, sec to int seconds. Round to seconds
    assert len(strtime) == 6
    sectime = int(strtime[0:2])*3600 + int(strtime[2:4])*60 + int(strtime[4:6])
    return sectime


def myDecode(stringValue):
# Parameter:
# stringValue:  String Wert, im Format Typ_Wert
#
# Rueckgabe:
# result:               Floatzahl

    splitValue = stringValue.split('_')

    if splitValue[0] == 'fl':
        #Hex >> Float
        result = struct.unpack('f',struct.pack('I',int('0x'+splitValue[1],0)))[0]
    elif splitValue[0] == 'u3':
        pass #TBD
    elif splitValue[0] == 'u8':
        pass #TBD

    return result

if __name__ == "__main__":
    logging.info('Starting Monitoring...')
    #status = 0  # 0: all off, 1: powerplug1, 2: powerplug2, 3: all activated
    # first check if it is daytime. If so, continue
    while True:
        if is_daytime():
            logging.debug('It is daytime')
            cp2g, cp2b, cp2h, batpercent =  get_current_power2grid_power2bat_housepower_batpercent()
            logging.info('gridpower: %s, batpower %s, homepower %s, batpercent: %s', cp2g, cp2b, cp2h, batpercent)
            # calculate available power. Account for possibly activated powerplugs
            available_power = cp2g

            dayhoursleft = is_daytime(return_hours_left=True)[1]
            logging.info("dayhoursleft: %s", dayhoursleft)


            # add energy consumed by plugs. Ignore if switched on manually
            plugconsumption = 0.
            if powerplug1[2]:
                plugconsumption += powerplug1[1]
            if powerplug2[2]:
                plugconsumption += powerplug2[1]
            # sanitycheck
            available_power += min(plugconsumption, cp2h)

            # subtract battery discharge or add fraction of battery charge if time left
            if cp2b <= 0:
                available_power += cp2b
            # if there are few hours left, also use part of batterychargingpower
            elif (dayhoursleft - 2) * 15 >= (100-batpercent) and batpercent > 20:
                available_power += cp2b * 0.8
            # subtract required charging power if not charged with full power
            elif batpercent < 85:
               available_power -= max(min(0.9 * maxchargingpower - cp2b, plugconsumption),0 )


            # start a bit earlier if battery still somewhat charged
            if dayhoursleft > 8 and batpercent > 20 and available_power < powerplug1[1]:
                available_power += 300

            logging.info('{:.2f} W available power.'.format(available_power))
            # now activate/deactivate the plugs. Only do, if they are not on internally (e.g. they were changed externally)
            # both plugs
            if available_power >= powerplug1[1]+powerplug2[1]:
                logging.info('Enough for both plugs')
                if not powerplug1[2]:
                    bridge.set_light(powerplug1[0], 'on', True)
                    powerplug1[2] = True
                if not powerplug2[2]:
                    bridge.set_light(powerplug2[0], 'on', True)
                    powerplug2[2] = True
            # higher power plug
            elif available_power >= powerplug2[1]:
                logging.info('Enough for {}'.format(powerplug2[0]))
                if not powerplug2[2]:
                    bridge.set_light(powerplug2[0], 'on', True)
                    powerplug2[2] = True
                if powerplug1[2]:
                    bridge.set_light(powerplug1[0], 'on', False)
                    powerplug1[2] = False
            # lower power plug
            elif available_power >= powerplug1[1]:
                logging.info('Enough for {}'.format(powerplug1[0]))
                if not powerplug1[2]:
                    bridge.set_light(powerplug1[0], 'on', True)
                    powerplug1[2] = True
                if powerplug2[2]:
                    bridge.set_light(powerplug2[0], 'on', False)
                    powerplug2[2] = False

            else:
                logging.info('Not enough for any plug. Deactivating all, even if switched on manually before')
                bridge.set_light(powerplug1[0], 'on', False)
                powerplug1[2] = False
                bridge.set_light(powerplug2[0], 'on', False)
                powerplug2[2] = False
                # sleep some more to avoid off-ons
                sleep(180)

            sleep(120)
        else:
            nighthoursleft = is_daytime(return_hours_left=True)[1]
            sleepseconds = max(int(nighthoursleft*3600), 100)
            logging.info('It is nighttime for %s more hours. Waiting for %s seconds.', nighthoursleft, sleepseconds)
            statuscounter = 0
            sleep(sleepseconds)
