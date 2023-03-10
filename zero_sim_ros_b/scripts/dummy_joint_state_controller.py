#!/usr/bin/env python

# Copyright (c) 2014, Kei Okada
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#
# 1. Redistributions of source code must retain the above copyright notice,
#    this list of conditions and the following disclaimer.
# 2. Redistributions in binary form must reproduce the above copyright
#    notice, this list of conditions and the following disclaimer in the
#    documentation and/or other materials provided with the distribution.
# 3. Neither the name of the Rethink Robotics nor the names of its
#    contributors may be used to endorse or promote products derived from
#    this software without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
# ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT OWNER OR CONTRIBUTORS BE
# LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
# CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
# SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
# INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
# CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
# ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.

import rospy
import copy
import threading
import Queue

import actionlib
import actionlib_msgs

# Publish
from sensor_msgs.msg import JointState

# Subscribe
from control_msgs.msg import FollowJointTrajectoryAction
from control_msgs.msg import FollowJointTrajectoryResult
from trajectory_msgs.msg import JointTrajectoryPoint


"""
This fake_joint_trajectory action server enable MoveIt! to execute planned path without running gazebo or robot contorller for real robot.
"""
class JointTrajectoryActionServer():
    """
    Someo the method defined in this class is copied from MotionControllerSimulator defined in https://github.com/ros-industrial/industrial_core/blob/40a72f3e1f0e63be2f4cb99348ab1d53a21c2fdf/industrial_robot_simulator/industrial_robot_simulator
    Basically JointTrajectoryActionServer is actionlib implementation of MotionControllerSimulator
    Constructor of motion controller simulator
    """
    def __init__(self, joint_namespace, joint_names, update_rate = 100, buffer_size = 0):
        # Class lock
        self.lock = threading.Lock()

        # start action server
        self._as = actionlib.ActionServer(joint_namespace+'/follow_joint_trajectory', FollowJointTrajectoryAction, self.trajectory_callback, auto_start = False)
        self._as.start()

        num_joints = len(joint_names)
        self.joint_names = joint_names

        # Motion loop update rate (higher update rates result in smoother simulated motion)
        self.update_rate = update_rate
        rospy.logdebug("Setting motion update rate (hz): %f", self.update_rate)

        # Initialize joint position
        self.joint_positions = [0]*num_joints
        rospy.logdebug("Setting initial joint state: %s", str(self.joint_positions))

        # Initialize motion buffer (contains joint position lists)
        self.motion_buffer = Queue.Queue(buffer_size)
        rospy.logdebug("Setting motion buffer size: %i", buffer_size)

        # Shutdown signal
        self.sig_shutdown = False

        # Stop signal
        self.sig_stop = False

        # Motion thread
        self.motion_thread = threading.Thread(target=self._motion_worker)
        self.motion_thread.daemon = True
        self.motion_thread.start()




    """
    Trajectory subscription callback (gets called whenever a joint trajectory
    is received).
    @param msg_in: joint trajectory message
    @type  msg_in: JointTrajectory
    """
    def trajectory_callback(self, goal):
        self._current_goal = goal
        msg_in = goal.get_goal()
        try:
            rospy.logdebug('Received trajectory with %s points, executing callback', str(len(msg_in.trajectory.points)))

            if self.is_in_motion():
                if len(msg_in.trajectory.points) > 0:
                    rospy.logerr('Received trajectory while still in motion, trajectory splicing not supported')
                else:
                    rospy.logdebug('Received empty trajectory while still in motion, stopping current trajectory')
                self.stop()
                self._current_goal.set_canceled(None, "This trajectory was canceled because another once was received")

            else:
                goal.set_accepted("This trajectory has been accepted");
                self._current_goal = goal

                for point in msg_in.trajectory.points:
                    point = self._to_controller_order(msg_in.trajectory.joint_names, point)
                    self.add_motion_waypoint(point)

        except Exception as e:
            rospy.logerr('Unexpected exception: %s', e)

        rospy.logdebug('Exiting trajectory callback')


    """
    Remaps point to controller joint order
    @param keys:   keys defining joint value order
    @type  keys:   list
    @param point:  joint trajectory point
    @type  point:  JointTrajectoryPoint
    @return point: reorder point
    @type point: JointTrajectoryPoint
    """
    def _to_controller_order(self, keys, point):
        #rospy.loginfo('to controller order, keys: %s, point: %s', str(keys), str(point))
        pt_rtn = copy.deepcopy(point)
        pt_rtn.positions = self._remap_order(self.joint_names, keys, point.positions)

        return pt_rtn

    """
    """
    def _remap_order(self, ordered_keys, value_keys, values, ordered_values = []):
        #rospy.loginfo('remap order, ordered_keys: %s, value_keys: %s, values: %s', str(ordered_keys), str(value_keys), str(values))
        
        if len(ordered_values) != len(ordered_keys) :
            ordered_values = [0]*len(ordered_keys)
        mapping = dict(zip(value_keys, values))
        #rospy.loginfo('maping: %s', str(mapping))

        for i in range(len(ordered_keys)):
            if mapping.has_key(ordered_keys[i]):
                ordered_values[i] = mapping[ordered_keys[i]]
            pass

        return ordered_values

    """
    """
    def add_motion_waypoint(self, point):
        self.motion_buffer.put(point)


    """
    """
    def get_joint_positions(self, full_joint_names, full_positions = []):
        with self.lock:
            return self._remap_order(full_joint_names, self.joint_names, self.joint_positions[:], full_positions)

    """
    """
    def is_in_motion(self):
        return not self.motion_buffer.empty()

    """
    """
    def shutdown(self):
        self.sig_shutdown = True
        rospy.logdebug('Motion_Controller shutdown signaled')

    """
    """
    def stop(self):
        rospy.logdebug('Motion_Controller stop signaled')
        with self.lock:
            self._clear_buffer()
            self.sig_stop = True

    """
    """
    def interpolate(self, last_pt, current_pt, alpha):
        intermediate_pt = JointTrajectoryPoint()
        for last_joint, current_joint in zip(last_pt.positions, current_pt.positions):
            intermediate_pt.positions.append(last_joint + alpha*(current_joint-last_joint))
        intermediate_pt.time_from_start = last_pt.time_from_start + rospy.Duration(alpha*(current_pt.time_from_start.to_sec() - last_pt.time_from_start.to_sec()))
        return intermediate_pt

    """
    """
    def _clear_buffer(self):
        with self.motion_buffer.mutex:
            self.motion_buffer.queue.clear()

    """
    """
    def _move_to(self, point, dur):
        rospy.sleep(dur)

        with self.lock:
            if not self.sig_stop:
                self.joint_positions = point.positions[:]
                #rospy.loginfo('Moved to position: %s in %s', str(self.joint_positions), str(dur))
            else:
                rospy.logdebug('Stopping motion immediately, clearing stop signal')
                self.sig_stop = False

    """
    """
    def _motion_worker(self):
        rospy.logdebug('Starting motion worker in motion controller simulator')
        move_duration = rospy.Duration()
        if self.update_rate <> 0.:
            update_duration = rospy.Duration(1./self.update_rate)
        last_goal_point = JointTrajectoryPoint()

        with self.lock:
            last_goal_point.positions = self.joint_positions[:]

        while not self.sig_shutdown:
            try:
                current_goal_point = self.motion_buffer.get()

                # If the current time from start is less than the last, then it's a new trajectory
                if current_goal_point.time_from_start < last_goal_point.time_from_start:
                    move_duration = current_goal_point.time_from_start

                # Else it's an existing trajectory and subtract the two
                else:
                    # If current move duration is greater than update_duration, move arm to interpolated joint position
                    # Provide an exception to this rule: if update rate is <=0, do not add interpolated points
                    move_duration = current_goal_point.time_from_start - last_goal_point.time_from_start
                    if self.update_rate > 0:
                        while update_duration < move_duration:
                            intermediate_goal_point = self.interpolate(last_goal_point, current_goal_point, update_duration.to_sec()/move_duration.to_sec())
                            self._move_to(intermediate_goal_point, update_duration.to_sec()) #TODO should this use min(update_duration, 0.5*move_duration) to smooth timing?
                            last_goal_point = copy.deepcopy(intermediate_goal_point)
                            move_duration = current_goal_point.time_from_start - intermediate_goal_point.time_from_start

                        if not self.is_in_motion() and self._current_goal.get_goal_status().status == actionlib_msgs.msg.GoalStatus.ACTIVE:
                            self._current_goal.set_succeeded(FollowJointTrajectoryResult(error_code = 0)) # ok

                self._move_to(current_goal_point, move_duration)
                last_goal_point = copy.deepcopy(current_goal_point)


            except Exception as e:
                rospy.logerr('Unexpected exception: %s', e)

        rospy.logdebug("Shutting down motion controller")




"""
JointTrajectoryControllerNode
This class simulates an joint trajectoin action.
"""
class JointTrajectoryControllerNode():
    """
    Constructor of joint trajectory action
    """
    def __init__(self):
        rospy.init_node('dummy_joint_trajectory_controller')

        # Class lock
        self.lock = threading.Lock()

        # Publish rate (hz)
        self.pub_rate = rospy.get_param('pub_rate', 10.0)
        rospy.logdebug("Setting publish rate (hz) based on parameter: %f", self.pub_rate)

        # Joint names
        self.joint_names = []
        controller_list = rospy.get_param('/move_group/controller_list')
        for controller in controller_list:
            print controller['type'], controller['name'], controller['joints']
            self.joint_names.extend(controller['joints'])
        if len(self.joint_names) == 0:
            rospy.logwarn("Joint list is empty, did you set controller_joint_name?")
        rospy.loginfo("Simulating manipulator with %d joints: %s", len(self.joint_names), ", ".join(self.joint_names))

        # Published to joint states
        rospy.logdebug("Creating joint state publisher")
        self.joint_state_pub = rospy.Publisher('joint_states', JointState, queue_size=1)

        # Subscribe to a joint trajectory
        rospy.logdebug("Creating joint trajectory action")
        self.joint_trajectory_actions = []
        for controller in controller_list:
            self.joint_trajectory_actions.append(JointTrajectoryActionServer(controller['name'], controller['joints']))

        # JointStates timed task (started automatically)
        period = rospy.Duration(1.0/self.pub_rate)
        rospy.logdebug('Setting up publish worker with period (sec): %s', str(period.to_sec()))
        rospy.Timer(period, self.publish_worker)

        # Clean up init
        for ac in self.joint_trajectory_actions:
            rospy.on_shutdown(ac.shutdown)


    """
    The publish worker is executed at a fixed rate.  This publishes the various
    state and status information for the robot.
    """
    def publish_worker(self, event):
        self.joint_state_publisher()


    """
    The joint state publisher publishes the current joint state and the current
    feedback state (as these are closely related)
    """
    def joint_state_publisher(self):
        try:
            joint_state_msg = JointState()
            time = rospy.Time.now()

            with self.lock:
                #Joint states
                joint_state_msg.header.stamp = time
                joint_state_msg.name = self.joint_names
                joint_positions = [0]*len(self.joint_names)
                for ac in self.joint_trajectory_actions:
                    joint_positions = ac.get_joint_positions(self.joint_names, joint_positions)
                joint_state_msg.position = joint_positions
                self.joint_state_pub.publish(joint_state_msg)

        except Exception as e:
            rospy.logerr('Unexpected exception in joint state publisher: %s', e)


if __name__ == '__main__':
    try:
        rospy.loginfo('Starting dummy_joint_trajectory_controller')
        controller = JointTrajectoryControllerNode()
        rospy.spin()
    except rospy.ROSInterruptException:
        pass