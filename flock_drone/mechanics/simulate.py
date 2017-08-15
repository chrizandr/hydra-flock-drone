"""Main control loop for drone."""
import os
import sys
curDir = os.path.dirname(__file__)
# this will return parent directory.
parentDir = os.path.abspath(os.path.join(curDir, os.pardir))
# this will return parent directory.
superParentDir = os.path.abspath(os.path.join(parentDir, os.pardir))
sys.path.insert(0, superParentDir)

import threading
import random
import re
from flock_drone.mechanics.main import get_drone, update_drone, get_controller_location, update_drone_at_controller

from flock_drone.mechanics.logs import (send_dronelog, send_http_api_log,
                                        gen_DroneLog, gen_HttpApiLog)

from flock_drone.mechanics.datastream import gen_Datastream, update_datastream, send_datastream
from flock_drone.mechanics.anomaly import gen_Anomaly, send_anomaly, get_anomaly, update_anomaly_at_controller, update_anomaly_locally
from flock_drone.mechanics.distance import get_new_coordinates, gen_square_path, deg2num, gen_pos_limits_from_square_path, is_valid_location, drone_reached_destination, get_direction
from flock_drone.mechanics.commands import get_command_collection, get_command, delete_commands

# Drone main Loop time settings
global LOOP_TIME, ITERATOR
LOOP_TIME = 15

CONTROLLER_LOC = tuple(float(x)
                       for x in get_controller_location()["Location"].split(","))

DRONE_BOUNDS = gen_pos_limits_from_square_path(
    gen_square_path(CONTROLLER_LOC, 10))


# Command related functions


def handle_drone_commands(drone):
    """Handle the commands on the drone server and update drone accordingly."""
    # Using the latest command, not following previously stored ones, server will ensure order
    commands = get_command_collection()
    command_ids = [x["@id"] for x in commands]
    temp_list = list()
    for id_ in command_ids:
        regex = r'/(.*)/(\d*)'
        matchObj = re.match(regex, id_)
        if matchObj:
            temp_list.append(matchObj.group(2))
    temp_list.sort()

    if len(temp_list) > 0:
        latest_command = get_command(temp_list[-1])
        # Execute the latest command
        drone = execute_command(latest_command, drone)
        # Delete after execution
        delete_commands(temp_list)

    return drone


def execute_command(command, drone):
    """Execute the command on the drone."""
    if command["DroneID"] == drone["DroneID"]:
        drone["State"] = command["State"]

    return drone


# Drone state related functions

def is_charging(drone):
    """Check if the drone status is charging."""
    return drone["State"]["Status"] == "Charging"


def is_not_off(drone):
    """Check if drone status is not off."""
    return drone["State"]["Status"] != "Off"


def is_confirming(drone):
    """Check if the drone is in confirmation state."""
    return drone["State"]["Status"] == "Confirming"


def is_inactive(drone):
    """Check if the drone is in inactive state."""
    return drone["State"]["Status"] == "Inactive"


def is_active(drone):
    """Check if the drone is in active state."""
    return drone["State"]["Status"] == "Active"


# Battery related functions
def discharge_drone_battery(drone):
    """Handle drone battery discharging."""
    battery_level = drone["State"]["Battery"]
    drone_identifier = drone["DroneID"]
    if float(battery_level) > 20:
        drone["State"]["Battery"] = int(
            drone["State"]["Battery"]) - 1
    elif float(battery_level) <= 20 and float(battery_level) > 3:
        # Drone in inactive state will take less battery per iteration (1/4 of normal battery usage).
        drone["State"]["Battery"] = float(
            drone["State"]["Battery"]) - 0.25

    if float(battery_level) == 20.0:
        drone["State"]["Status"] = "Inactive"
        drone["State"]["Battery"] = int(
            drone["State"]["Battery"]) - 1

        dronelog = gen_DroneLog("Drone %s" % (str(
            drone_identifier),), "battery Low %s, changing to Inactive state" % (str(drone["State"]["Battery"])))
        send_dronelog(dronelog)

        http_api_log = gen_HttpApiLog("Drone %s" % (
            str(drone_identifier)), "PUT DroneLog", "Controller")
        send_http_api_log(http_api_log)

    elif float(battery_level) == 4.0:
        # Battery level critical change drone status to OFF
        drone["State"]["Status"] = "Off"

        dronelog = gen_DroneLog("Drone %s" % (
            str(drone_identifier)), "Battery level critical, shutting down!")
        send_dronelog(dronelog)

        http_api_log = gen_HttpApiLog("Drone %s" % (
            str(drone_identifier)), "PUT DroneLog", "Controller")
        send_http_api_log(http_api_log)

    return drone


def charge_drone_battery(drone):
    """Handle the drone battery charging operation."""
    battery_level = drone["State"]["Battery"]
    if float(battery_level) < 95:
        # Increase battery level
        drone["State"]["Battery"] = float(battery_level) + 5
    else:
        # If battery >= 95 set battery level to 100%
        drone["State"]["Battery"] = 100

        dronelog = gen_DroneLog("Drone %s" % (
            str(drone["DroneID"])), "charging complete, returning to Active state")
        send_dronelog(dronelog)

        drone["State"]["Status"] = "Active"

        http_api_log = gen_HttpApiLog("Drone %s" % (
            str(drone["DroneID"])), "PUT DroneLog", "Controller")
        send_http_api_log(http_api_log)

    return drone


def handle_drone_battery(drone):
    """Handle the drone battery status."""
    if is_charging(drone):
        drone = charge_drone_battery(drone)
    else:
        drone = discharge_drone_battery(drone)
    return drone


# Distance related functions
def get_new_direction_for_drone(current_direction):
    """Return a new direction for drone."""
    directions = ["N", "E", "S", "W"]
    directions.pop(directions.index(current_direction))

    return random.choice(directions)


def calculate_dis_travelled(speed, time):
    """Calculate the distance travelled(in Km) in a give amount of time(s)."""
    return (speed * time) / 3600.0


def handle_invalid_pos(drone, distance_travelled):
    """Handle invalid position update for drone."""
    current_direction = drone["State"]["Direction"]

    direction = get_new_direction_for_drone(current_direction)
    drone["State"]["Direction"] = direction

    drone_position = tuple(float(a)
                           for a in drone["State"]["Position"].split(","))
    new_drone_position = get_new_coordinates(
        drone_position, distance_travelled, direction)
    if is_valid_location(new_drone_position, DRONE_BOUNDS):
        drone["State"]["Position"] = ",".join(
            map(str, new_drone_position))
        return drone
    else:
        return handle_invalid_pos(drone, distance_travelled)


def update_drone_position(drone, distance_travelled, direction):
    """Update the drone position given the distance travelled and direction of travel."""
    drone_position = tuple(float(a)
                           for a in drone["State"]["Position"].split(","))
    new_drone_position = get_new_coordinates(
        drone_position, distance_travelled, direction)
    if is_valid_location(new_drone_position, DRONE_BOUNDS):
        drone["State"]["Position"] = ",".join(
            map(str, new_drone_position))
    else:
        drone = handle_invalid_pos(drone, distance_travelled)
    return drone


def handle_drone_position(drone):
    """Handle the drone position changes."""
    if not is_charging(drone):
        drone_speed = float(drone["State"]["Speed"])
        distance_travelled = calculate_dis_travelled(drone_speed, LOOP_TIME)
        drone_direction = str(drone["State"]["Direction"])
        drone_identifier = drone["DroneID"]
        drone = update_drone_position(
            drone, distance_travelled, drone_direction)

        if(drone["State"]["Direction"] != drone_direction):

            dronelog = gen_DroneLog("Drone %s" % (str(drone_identifier),),
                                    "changed direction to %s" % (str(drone["State"]["Direction"])))
            send_dronelog(dronelog)

            http_api_log = gen_HttpApiLog("Drone %s" % (str(drone_identifier)),
                                          "PUT DroneLog", "Controller")
            send_http_api_log(http_api_log)

    return drone


# Datastream related functions
def gen_normal_sensor_data():
    """Generate normal sensor data for drone datastream."""
    normal_range = range(25, 40)
    return random.choice(normal_range)


def gen_abnormal_sensor_data():
    """Generate abnormal sensor data for drone datastream."""
    abnormal_range = range(45, 60)
    return random.choice(abnormal_range)


# Anomaly related functions
def gen_grid_anomaly(drone):
    """Generate an anomaly using drone location and a set of probabilities."""
    drone_location = tuple(float(a)
                           for a in drone["State"]["Position"].split(","))

    xtile, ytile = deg2num(drone_location[0], drone_location[1], 17)

    ## Test for anomaly genration test = 5x + 7y + 1
    test = (5*int(xtile)) + (7*(ytile)) + 1
    print("ANOMALY GRID TEST", test, test%5, test%7)

    if test % 3 == 0 and test % 7 ==0:
        ## if mod 3 == 0 or mod 7 ==0 then probability of anomaly = 1/2
        option = random.choice([True, True, False, False, False, True])
    else:
        option = False

    if option:
        anomaly = gen_Anomaly(
            drone["State"]["Position"], drone["DroneID"])
        return anomaly
    return None


def handle_anomaly(drone):
    """Handle the anomaly that the drone needs to check on."""
    anomaly = get_anomaly()
    if anomaly is not None:
        destination = tuple(float(a) for a in anomaly["Location"].split(","))
        if not drone_reached_destination(drone, destination):
            print("Drone moving toward anomaly")
            source = tuple(float(a)
                           for a in drone["State"]["Position"].split(","))
            new_direction = get_direction(source, destination)
            print(new_direction)
            if new_direction != drone["State"]["Direction"]:
                drone["State"]["Direction"] = new_direction

                dronelog = gen_DroneLog("Drone %s" % (str(drone["DroneID"]),),
                                        "changed direction to %s" % (str(new_direction)))
                send_dronelog(dronelog)

        else:
            # if reached destination
            print("Drone reached destination")
            dronelog = gen_DroneLog("Drone %s" % (str(drone["DroneID"])),
                                    "reached anomaly location, scanning")
            send_dronelog(dronelog)
            ## Check if anomaly exists at that location
            confirm_anomaly = gen_grid_anomaly(drone)
            if confirm_anomaly is not None:
                anomaly["Status"] = "Positive"

                dronelog = gen_DroneLog("Drone %s" % (str(drone["DroneID"])),
                                        "detected POSITIVE anomaly.")
                send_dronelog(dronelog)

            else:
                anomaly["Status"] = "Negative"

                dronelog = gen_DroneLog("Drone %s" % (str(drone["DroneID"])),
                                        "detected NEGATIVE anomaly.")
                send_dronelog(dronelog)

            print("Updating anomaly locally")
            update_anomaly_locally(anomaly, drone["DroneID"])

            print("Updating anomaly at controller")
            update_anomaly_at_controller(
                anomaly, anomaly["AnomalyID"], drone["DroneID"])
            print("Anomaly confirmation done")

            drone["State"]["Status"] = "Active"

        http_api_log = gen_HttpApiLog("Drone %s" % (str(drone["DroneID"])),
                                      "PUT DroneLog", "Controller")
        send_http_api_log(http_api_log)

    return drone


def handle_drone_low_battery(drone):
    """Handle the drone inactive state ( when 3< battery < 20)."""
    destination = CONTROLLER_LOC
    if not drone_reached_destination(drone, destination):
        print("Drone moving toward central controller")
        source = tuple(float(a)
                       for a in drone["State"]["Position"].split(","))
        new_direction = get_direction(source, destination)
        print(new_direction)
        if new_direction != drone["State"]["Direction"]:
            drone["State"]["Direction"] = new_direction

            dronelog = gen_DroneLog("Drone %s" % (str(drone["DroneID"]),),
                                    "changed direction to %s" % (str(new_direction)))
            send_dronelog(dronelog)

    else:
        # if reached destination
        dronelog = gen_DroneLog("Drone %s" % (str(drone["DroneID"]),),
                                "reached central controller, charging.")
        send_dronelog(dronelog)

        print("Drone reached destination")
        drone["State"]["Status"] = "Charging"
    return drone


def main():
    """Main 15 second time loop for drone mechanics."""
    try:
        print("Retrieving the drone details")
        drone = get_drone()
        print(drone)

        if is_not_off(drone):

            drone_identifier = drone["DroneID"]
            datastream = None
            anomaly = get_anomaly()
            if anomaly is not None:
                if anomaly["Status"] == "Confirming" and drone["State"]["Status"] == "Active":
                    drone["State"]["Status"] = "Confirming"

            if is_confirming(drone):
                print("Drone handling anomaly")
                drone = handle_anomaly(drone)

            elif is_inactive(drone):
                print("Drone battery low, needs to charge")
                drone = handle_drone_low_battery(drone)

            elif is_active(drone):
                anomaly = gen_grid_anomaly(drone)
                if anomaly is not None:
                    print("New anomaly created")
                    send_anomaly(anomaly, drone_identifier)
                    datastream = gen_Datastream(gen_abnormal_sensor_data(
                    ), drone["State"]["Position"], drone_identifier)
                else:
                    datastream = gen_Datastream(gen_normal_sensor_data(
                    ), drone["State"]["Position"], drone_identifier)

            # Handle positions and battery change
            drone = handle_drone_battery(drone)
            drone = handle_drone_position(drone)
            drone = handle_drone_commands(drone)

            # update the drone both locally and on the controller
            update_drone(drone)

            print(update_drone_at_controller(drone, drone_identifier))

            if datastream is not None:
                # Send datastream to central controller
                send_datastream(datastream)
                # Update datastream locally
                update_datastream(datastream)

    except Exception as e:
        print(e)

    # call main() again in LOOP_TIME
    threading.Timer(LOOP_TIME, main).start()


if __name__ == "__main__":
    # anomaly = gen_Anomaly("0.956901647439813,14.08447265625", "22")
    # send_anomaly(anomaly, "22")
    main()
