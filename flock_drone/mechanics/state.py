"""Operation related to Drone state POST operations."""
# from flock_drone.mechanics.main import RES_CS
from flock_drone.mechanics.main import get_drone, update_drone


def gen_State(drone_id, battery, direction, position, sensor_status, speed):
    """Generate a State objects."""
    state = {
        "@type": "State",
        "DroneID": drone_id,
        "Battery": battery,
        "Direction": direction,
        "Position": position,
        "SensorStatus": sensor_status,
        "Speed": speed,
    }
    return state


def update_state(state):
    """Update the drone state on drone server."""
    drone = get_drone()
    if int(drone["DroneID"]) == state["DroneID"]:
        # Remove the DroneID key from state
        state.pop("DroneID", None)

        # Update the drone state
        drone["DroneState"] = state
        update_drone(drone)
        print("Drone state updated successfully.")
    else:
        print("ERROR: DroneID %s not valid." % (state["DroneID"]))


def get_state():
    """Get the current drone state from the drone server."""
    drone = get_drone()
    drone_state = drone["DroneState"]
    drone_state["DroneID"] = drone["DroneID"]

    return drone_state