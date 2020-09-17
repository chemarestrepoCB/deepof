# @author lucasmiranda42
# encoding: utf-8
# module deepof

"""

Functions and general utilities for the deepof package. See documentation for details

"""

import cv2
import matplotlib.pyplot as plt
import multiprocessing
import networkx as nx
import numpy as np
import os
import pandas as pd
import regex as re
import seaborn as sns
from copy import deepcopy
from itertools import combinations, product
from joblib import Parallel, delayed
from scipy import spatial
from scipy import stats
from sklearn import mixture
from tqdm import tqdm
from typing import Tuple, Any, List, Union, NewType

# DEFINE CUSTOM ANNOTATED TYPES #


Coordinates = NewType("Coordinates", Any)


# CONNECTIVITY FOR DLC MODELS


def connect_mouse_topview(animal_id=None) -> nx.Graph:
    """Creates a nx.Graph object with the connectivity of the bodyparts in the
    DLC topview model for a single mouse. Used later for angle computing, among others

        Parameters:
            - animal_id (str): if more than one animal is tagged,
            specify the animal identyfier as a string

        Returns:
            - connectivity (nx.Graph)"""

    connectivity = {
        "Nose": ["Left_ear", "Right_ear", "Spine_1"],
        "Left_ear": ["Right_ear", "Spine_1"],
        "Right_ear": ["Spine_1"],
        "Spine_1": ["Center", "Left_fhip", "Right_fhip"],
        "Center": ["Left_fhip", "Right_fhip", "Spine_2", "Left_bhip", "Right_bhip"],
        "Spine_2": ["Left_bhip", "Right_bhip", "Tail_base"],
        "Tail_base": ["Tail_1", "Left_bhip", "Right_bhip"],
        "Tail_1": ["Tail_2"],
        "Tail_2": ["Tail_tip"],
    }

    connectivity = nx.Graph(connectivity)

    if animal_id:
        mapping = {
            node: "{}_{}".format(animal_id, node) for node in connectivity.nodes()
        }
        nx.relabel_nodes(connectivity, mapping, copy=False)

    return connectivity


# QUALITY CONTROL AND PREPROCESSING #


def likelihood_qc(dframe: pd.DataFrame, threshold: float = 0.9) -> np.array:
    """Returns a DataFrame filtered dataframe, keeping only the rows entirely above the threshold.

        Parameters:
            - dframe (pandas.DataFrame): DeepLabCut output, with positions over time and associated likelihhod
            - threshold (float): minimum acceptable confidence

        Returns:
            - filt_mask (np.array): mask on the rows of dframe"""

    Likes = np.array([dframe[i]["likelihood"] for i in list(dframe.columns.levels[0])])
    Likes = np.nan_to_num(Likes, nan=1.0)
    filt_mask = np.all(Likes > threshold, axis=0)

    return filt_mask


def bp2polar(tab: pd.DataFrame) -> pd.DataFrame:
    """Returns the DataFrame in polar coordinates.

        Parameters:
            - tab (pandas.DataFrame):Table with cartesian coordinates

        Returns:
            - polar (pandas.DataFrame): Equivalent to input, but with values in polar coordinates"""

    tab_ = np.array(tab)
    complex_ = tab_[:, 0] + 1j * tab_[:, 1]
    polar = pd.DataFrame(np.array([abs(complex_), np.angle(complex_)]).T)
    polar.rename(columns={0: "rho", 1: "phi"}, inplace=True)
    return polar


def tab2polar(cartesian_df: pd.DataFrame) -> pd.DataFrame:
    """Returns a pandas.DataFrame in which all the coordinates are polar.

        Parameters:
            - cartesian_df (pandas.DataFrame):DataFrame containing tables with cartesian coordinates

        Returns:
            - result (pandas.DataFrame): Equivalent to input, but with values in polar coordinates"""

    result = []
    for df in list(cartesian_df.columns.levels[0]):
        result.append(bp2polar(cartesian_df[df]))
    result = pd.concat(result, axis=1)
    idx = pd.MultiIndex.from_product(
        [list(cartesian_df.columns.levels[0]), ["rho", "phi"]],
        names=["bodyparts", "coords"],
    )
    result.columns = idx
    return result


def compute_dist(
    pair_array: np.array, arena_abs: int = 1, arena_rel: int = 1
) -> pd.DataFrame:
    """Returns a pandas.DataFrame with the scaled distances between a pair of body parts.

        Parameters:
            - pair_array (numpy.array): np.array of shape N * 4 containing X,y positions
            over time for a given pair of body parts
            - arena_abs (int): diameter of the real arena in cm
            - arena_rel (int): diameter of the captured arena in pixels

        Returns:
            - result (pd.DataFrame): pandas.DataFrame with the
            absolute distances between a pair of body parts"""

    lim = 2 if pair_array.shape[1] == 4 else 1
    a, b = pair_array[:, :lim], pair_array[:, lim:]
    ab = a - b

    dist = np.sqrt(np.einsum("...i,...i", ab, ab))
    return pd.DataFrame(dist * arena_abs / arena_rel)


def bpart_distance(
    dataframe: pd.DataFrame, arena_abs: int = 1, arena_rel: int = 1
) -> pd.DataFrame:
    """Returns a pandas.DataFrame with the scaled distances between all pairs of body parts.

        Parameters:
            - dataframe (pandas.DataFrame): pd.DataFrame of shape N*(2*bp) containing X,y positions
        over time for a given set of bp body parts
            - arena_abs (int): diameter of the real arena in cm
            - arena_rel (int): diameter of the captured arena in pixels

        Returns:
            - result (pd.DataFrame): pandas.DataFrame with the
            absolute distances between all pairs of body parts"""

    indexes = combinations(dataframe.columns.levels[0], 2)
    dists = []
    for idx in indexes:
        dist = compute_dist(np.array(dataframe.loc[:, list(idx)]), arena_abs, arena_rel)
        dist.columns = [idx]
        dists.append(dist)

    return pd.concat(dists, axis=1)


def angle(a: np.array, b: np.array, c: np.array) -> np.array:
    """Returns a numpy.array with the angles between the provided instances.

        Parameters:
            - a (2D np.array): positions over time for a bodypart
            - b (2D np.array): positions over time for a bodypart
            - c (2D np.array): positions over time for a bodypart

        Returns:
            - ang (1D np.array): angles between the three-point-instances"""

    ba = a - b
    bc = c - b

    cosine_angle = np.einsum("...i,...i", ba, bc) / (
        np.linalg.norm(ba, axis=1) * np.linalg.norm(bc, axis=1)
    )
    ang = np.arccos(cosine_angle)

    return ang


def angle_trio(bpart_array: np.array) -> np.array:
    """Returns a numpy.array with all three possible angles between the provided instances.

        Parameters:
            - bpart_array (2D numpy.array): positions over time for a bodypart

        Returns:
            - ang_trio (2D numpy.array): all-three angles between the three-point-instances"""

    a, b, c = bpart_array
    ang_trio = np.array([angle(a, b, c), angle(a, c, b), angle(b, a, c)])

    return ang_trio


def rotate(
    p: np.array, angles: np.array, origin: np.array = np.array([0, 0])
) -> np.array:
    """Returns a numpy.array with the initial values rotated by angles radians

        Parameters:
            - p (2D numpy.array): array containing positions of bodyparts over time
            - angles (2D numpy.array): set of angles (in radians) to rotate p with
            - origin (2D numpy.array): rotation axis (zero vector by default)

        Returns:
            - rotated (2D numpy.array): rotated positions over time"""

    R = np.array([[np.cos(angles), -np.sin(angles)], [np.sin(angles), np.cos(angles)]])

    o = np.atleast_2d(origin)
    p = np.atleast_2d(p)

    rotated = np.squeeze((R @ (p.T - o.T) + o.T).T)

    return rotated


def align_trajectories(data: np.array, mode: str = "all") -> np.array:
    """Returns a numpy.array with the positions rotated in a way that the center (0 vector)
    and the body part in the first column of data are aligned with the y axis.

        Parameters:
            - data (3D numpy.array): array containing positions of body parts over time, where
            shape is N (sliding window instances) * m (sliding window size) * l (features)
            - mode (string): specifies if *all* instances of each sliding window get
            aligned, or only the *center*

        Returns:
            - aligned_trajs (2D np.array): aligned positions over time"""

    print(data.shape, mode)

    angles = np.zeros(data.shape[0])
    data = deepcopy(data)
    dshape = data.shape

    if mode == "center":
        center_time = (data.shape[1] - 1) // 2
        angles = np.arctan2(data[:, center_time, 0], data[:, center_time, 1])
    elif mode == "all":
        data = data.reshape(-1, dshape[-1], order="C")
        angles = np.arctan2(data[:, 0], data[:, 1])
    elif mode == "none":
        data = data.reshape(-1, dshape[-1], order="C")
        angles = np.zeros(data.shape[0])

    aligned_trajs = np.zeros(data.shape)

    for frame in range(data.shape[0]):
        aligned_trajs[frame] = rotate(
            data[frame].reshape([-1, 2], order="C"), angles[frame],
        ).reshape(data.shape[1:], order="C")

    if mode == "all" or mode == "none":
        aligned_trajs = aligned_trajs.reshape(dshape, order="C")

    return aligned_trajs


def smooth_boolean_array(a: np.array) -> np.array:
    """Returns a boolean array in which isolated appearances of a feature are smoothened

        Parameters:
            - a (1D numpy.array): boolean instances

        Returns:
            - a (1D numpy.array): smoothened boolean instances"""

    for i in range(1, len(a) - 1):
        if a[i - 1] == a[i + 1]:
            a[i] = a[i - 1]
    return a == 1


def rolling_window(a: np.array, window_size: int, window_step: int) -> np.array:
    """Returns a 3D numpy.array with a sliding-window extra dimension

        Parameters:
            - a (2D np.array): N (instances) * m (features) shape

        Returns:
            - rolled_a (3D np.array):
            N (sliding window instances) * l (sliding window size) * m (features)"""

    shape = (a.shape[0] - window_size + 1, window_size) + a.shape[1:]
    strides = (a.strides[0],) + a.strides
    rolled_a = np.lib.stride_tricks.as_strided(
        a, shape=shape, strides=strides, writeable=True
    )[::window_step]
    return rolled_a


def smooth_mult_trajectory(series: np.array, alpha: float = 0.15) -> np.array:
    """Returns a smooths a trajectory using exponentially weighted averages

        Parameters:
            - series (numpy.array): 1D trajectory array with N (instances) - alpha (float): 0 <= alpha <= 1;
            indicates the inverse weight assigned to previous observations. Higher (alpha~1) indicates less smoothing;
            lower indicates more (alpha~0)

        Returns:
            - smoothed_series (np.array): smoothed version of the input, with equal shape"""

    result = [series[0]]
    for n in range(len(series)):
        result.append(alpha * series[n] + (1 - alpha) * result[n - 1])

    smoothed_series = np.array(result)

    return smoothed_series


# BEHAVIOUR RECOGNITION FUNCTIONS #


def close_single_contact(
    pos_dframe: pd.DataFrame,
    left: str,
    right: str,
    tol: float,
    arena_abs: int,
    arena_rel: int,
) -> np.array:
    """Returns a boolean array that's True if the specified body parts are closer than tol.

        Parameters:
            - pos_dframe (pandas.DataFrame): DLC output as pandas.DataFrame; only applicable
            to two-animal experiments.
            - left (string): First member of the potential contact
            - right (string): Second member of the potential contact
            - tol (float): maximum distance for which a contact is reported
            - arena_abs (int): length in mm of the diameter of the real arena
            - arena_rel (int): length in pixels of the diameter of the arena in the video

        Returns:
            - contact_array (np.array): True if the distance between the two specified points
            is less than tol, False otherwise"""

    close_contact = (
        np.linalg.norm(pos_dframe[left] - pos_dframe[right], axis=1) * arena_abs
    ) / arena_rel < tol

    return close_contact


def close_double_contact(
    pos_dframe: pd.DataFrame,
    left1: str,
    left2: str,
    right1: str,
    right2: str,
    tol: float,
    arena_abs: int,
    arena_rel: int,
    rev: bool = False,
) -> np.array:
    """Returns a boolean array that's True if the specified body parts are closer than tol.

        Parameters:
            - pos_dframe (pandas.DataFrame): DLC output as pandas.DataFrame; only applicable
            to two-animal experiments.
            - left1 (string): First contact point of animal 1
            - left2 (string): Second contact point of animal 1
            - right1 (string): First contact point of animal 2
            - right2 (string): Second contact point of animal 2
            - tol (float): maximum distance for which a contact is reported
            - arena_abs (int): length in mm of the diameter of the real arena
            - arena_rel (int): length in pixels of the diameter of the arena in the video
            - rev (bool): reverses the default behaviour (nose2tail contact for both mice)

        Returns:
            - double_contact (np.array): True if the distance between the two specified points
            is less than tol, False otherwise"""

    if rev:
        double_contact = (
            (np.linalg.norm(pos_dframe[right1] - pos_dframe[left2], axis=1) * arena_abs)
            / arena_rel
            < tol
        ) & (
            (np.linalg.norm(pos_dframe[right2] - pos_dframe[left1], axis=1) * arena_abs)
            / arena_rel
            < tol
        )

    else:
        double_contact = (
            (np.linalg.norm(pos_dframe[right1] - pos_dframe[left1], axis=1) * arena_abs)
            / arena_rel
            < tol
        ) & (
            (np.linalg.norm(pos_dframe[right2] - pos_dframe[left2], axis=1) * arena_abs)
            / arena_rel
            < tol
        )

    return double_contact


def recognize_arena(
    videos: list,
    vid_index: int,
    path: str = ".",
    recoglimit: int = 1,
    arena_type: str = "circular",
) -> Tuple[np.array, int, int]:
    """Returns numpy.array with information about the arena recognised from the first frames
    of the video. WARNING: estimates won't be reliable if the camera moves along the video.

        Parameters:
            - videos (list): relative paths of the videos to analise
            - vid_index (int): element of videos to use
            - path (string): full path of the directory where the videos are
            - recoglimit (int): number of frames to use for position estimates
            - arena_type (string): arena type; must be one of ['circular']

        Returns:
            - arena (np.array): 1D-array containing information about the arena.
            "circular" (3-element-array) -> x-y position of the center and the radius
            - h (int): height of the video in pixels
            - w (int): width of the video in pixels"""

    cap = cv2.VideoCapture(os.path.join(path, videos[vid_index]))

    # Loop over the first frames in the video to get resolution and center of the arena
    arena, fnum, h, w = False, 0, None, None

    while cap.isOpened() and fnum < recoglimit:
        ret, frame = cap.read()
        # if frame is read correctly ret is True
        if not ret:
            print("Can't receive frame (stream end?). Exiting ...")
            break

        if arena_type == "circular":

            # Detect arena and extract positions
            arena = circular_arena_recognition(frame)[0]
            if h is None and w is None:
                h, w = frame.shape[0], frame.shape[1]

        fnum += 1

    cap.release()
    cv2.destroyAllWindows()

    return arena, h, w


def circular_arena_recognition(frame: np.array) -> np.array:
    """Returns x,y position of the center and the radius of the recognised arena

        Parameters:
            - frame (np.array): numpy.array representing an individual frame of a video

        Returns:
            - circles (np.array): 3-element-array containing x,y positions of the center
            of the arena, and a third value indicating the radius"""

    # Convert image to greyscale, threshold it, blur it and detect the biggest best fitting circle
    # using the Hough algorithm
    gray_image = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    ret, thresh = cv2.threshold(gray_image, 50, 255, 0)
    frame = cv2.medianBlur(thresh, 9)
    circle = cv2.HoughCircles(
        frame,
        cv2.HOUGH_GRADIENT,
        1,
        300,
        param1=50,
        param2=10,
        minRadius=0,
        maxRadius=0,
    )

    circles = []

    if circle is not None:
        circle = np.uint16(np.around(circle[0]))
        circles.append(circle)

    return circles[0]


def climb_wall(
    arena_type: str, arena: np.array, pos_dict: pd.DataFrame, tol: float, nose: str
) -> np.array:
    """Returns True if the specified mouse is climbing the wall

        Parameters:
            - arena_type (str): arena type; must be one of ['circular']
            - arena (np.array): contains arena location and shape details
            - pos_dict (table_dict): position over time for all videos in a project
            - tol (float): minimum tolerance to report a hit
            - nose (str): indicates the name of the body part representing the nose of
            the selected animal

        Returns:
            - climbing (np.array): boolean array. True if selected animal
            is climbing the walls of the arena"""

    nose = pos_dict[nose]

    if arena_type == "circular":
        center = np.array(arena[:2])
        climbing = np.linalg.norm(nose - center, axis=1) > (arena[2] + tol)

    else:
        raise NotImplementedError("Supported values for arena_type are ['circular']")

    return climbing


def rolling_speed(
    dframe: pd.DatetimeIndex,
    window: int = 5,
    rounds: int = 10,
    deriv: int = 1,
    center: str = None,
    typ: str = "coords",
) -> pd.DataFrame:
    """Returns the average speed over n frames in pixels per frame

        Parameters:
            - dframe (pandas.DataFrame): position over time dataframe
            - pause (int):  frame-length of the averaging window
            - rounds (int): float rounding decimals
            - deriv (int): position derivative order; 1 for speed,
            2 for acceleration, 3 for jerk, etc
            - center (str): for internal usage only; solves an issue
            with pandas.MultiIndex that arises when centering frames
            to a specific body part

        Returns:
            - speeds (pd.DataFrame): containing 2D speeds for each body part
            in the original data or their consequent derivatives"""

    original_shape = dframe.shape
    if center:
        body_parts = [bp for bp in dframe.columns.levels[0] if bp != center]
    else:
        try:
            body_parts = dframe.columns.levels[0]
        except AttributeError:
            body_parts = dframe.columns

    speeds = pd.DataFrame

    for der in range(deriv):

        features = 2 if der == 0 and typ == "coords" else 1

        distances = np.concatenate(
            [
                np.array(dframe).reshape([-1, features], order="F"),
                np.array(dframe.shift()).reshape([-1, features], order="F"),
            ],
            axis=1,
        )

        distances = np.array(compute_dist(distances))
        distances = distances.reshape(
            [
                original_shape[0],
                (original_shape[1] // 2 if typ == "coords" else original_shape[1]),
            ],
            order="F",
        )
        distances = pd.DataFrame(distances, index=dframe.index)
        speeds = np.round(distances.rolling(window).mean(), rounds)
        speeds[np.isnan(speeds)] = 0.0

        dframe = speeds

    speeds.columns = body_parts

    return speeds


def huddle(
    pos_dframe: pd.DataFrame,
    speed_dframe: pd.DataFrame,
    tol_forward: float,
    tol_spine: float,
    tol_speed: float,
    animal_id: str = "",
) -> np.array:
    """Returns true when the mouse is huddling using simple rules. (!!!) Designed to
    work with deepof's default DLC mice models; not guaranteed to work otherwise.

        Parameters:
            - pos_dframe (pandas.DataFrame): position of body parts over time
            - speed_dframe (pandas.DataFrame): speed of body parts over time
            - tol_forward (float): Maximum tolerated distance between ears and
            forward limbs
            - tol_rear (float): Maximum tolerated average distance between spine
            body parts
            - tol_speed (float): Maximum tolerated speed for the center of the mouse

        Returns:
            hudd (np.array): True if the animal is huddling, False otherwise
        """

    if animal_id != "":
        animal_id += "_"

    forward = (
        np.linalg.norm(
            pos_dframe[animal_id + "Left_ear"] - pos_dframe[animal_id + "Left_fhip"],
            axis=1,
        )
        < tol_forward
    ) & (
        np.linalg.norm(
            pos_dframe[animal_id + "Right_ear"] - pos_dframe[animal_id + "Right_fhip"],
            axis=1,
        )
        < tol_forward
    )

    spine = [
        animal_id + "Spine_1",
        animal_id + "Center",
        animal_id + "Spine_2",
        animal_id + "Tail_base",
    ]
    spine_dists = []
    for comb in range(2):
        spine_dists.append(
            np.linalg.norm(
                pos_dframe[spine[comb]] - pos_dframe[spine[comb + 1]], axis=1
            )
        )
    spine = np.mean(spine_dists) < tol_spine
    speed = speed_dframe[animal_id + "Center"] < tol_speed
    hudd = forward & spine & speed

    return hudd


def following_path(
    distance_dframe: pd.DataFrame,
    position_dframe: pd.DataFrame,
    follower: str,
    followed: str,
    frames: int = 20,
    tol: float = 0,
) -> np.array:
    """For multi animal videos only. Returns True if 'follower' is closer than tol to the path that
    followed has walked over the last specified number of frames

        Parameters:
            - distance_dframe (pandas.DataFrame): distances between bodyparts; generated by the preprocess module
            - position_dframe (pandas.DataFrame): position of bodyparts; generated by the preprocess module
            - follower (str) identifier for the animal who's following
            - followed (str) identifier for the animal who's followed
            - frames (int) frames in which to track whether the process consistently occurs,
            - tol (float) Maximum distance for which True is returned

        Returns:
            - follow (np.array): boolean sequence, True if conditions are fulfilled, False otherwise"""

    # Check that follower is close enough to the path that followed has passed though in the last frames
    shift_dict = {
        i: position_dframe[followed + "_Tail_base"].shift(i) for i in range(frames)
    }
    dist_df = pd.DataFrame(
        {
            i: np.linalg.norm(
                position_dframe[follower + "_Nose"] - shift_dict[i], axis=1
            )
            for i in range(frames)
        }
    )

    # Check that the animals are oriented follower's nose -> followed's tail
    right_orient1 = (
        distance_dframe[tuple(sorted([follower + "_Nose", followed + "_Tail_base"]))]
        < distance_dframe[
            tuple(sorted([follower + "_Tail_base", followed + "_Tail_base"]))
        ]
    )

    right_orient2 = (
        distance_dframe[tuple(sorted([follower + "_Nose", followed + "_Tail_base"]))]
        < distance_dframe[tuple(sorted([follower + "_Nose", followed + "_Nose"]))]
    )

    follow = np.all(
        np.array([(dist_df.min(axis=1) < tol), right_orient1, right_orient2]), axis=0,
    )

    return follow


def single_behaviour_analysis(
    behaviour_name: str,
    treatment_dict: dict,
    behavioural_dict: dict,
    plot: int = 0,
    stat_tests: bool = True,
    save: str = None,
    ylim: float = None,
) -> list:
    """Given the name of the behaviour, a dictionary with the names of the groups to compare, and a dictionary
       with the actual tags, outputs a box plot and a series of significance tests amongst the groups

        Parameters:
            - behaviour_name (str): name of the behavioural trait to analize
            - treatment_dict (dict): dictionary containing video names as keys and experimental conditions as values
            - behavioural_dict (dict): tagged dictionary containing video names as keys and annotations as values
            - plot (int): Silent if 0; otherwise, indicates the dpi of the figure to plot
            - stat_tests (bool): performs FDR corrected Mann-U non-parametric tests among all groups if True
            - save (str): Saves the produced figure to the specified file
            - ylim (float): y-limit for the boxplot. Ignored if plot == False

        Returns:
            - beh_dict (dict): dictionary containing experimental conditions as keys and video names as values
            - stat_dict (dict): dictionary containing condition pairs as keys and stat results as values"""

    beh_dict = {condition: [] for condition in treatment_dict.keys()}

    for condition in beh_dict.keys():
        for ind in treatment_dict[condition]:
            beh_dict[condition].append(
                np.sum(behavioural_dict[ind][behaviour_name])
                / len(behavioural_dict[ind][behaviour_name])
            )

    return_list = [beh_dict]

    if plot > 0:

        fig, ax = plt.subplots(dpi=plot)

        sns.boxplot(
            list(beh_dict.keys()), list(beh_dict.values()), orient="vertical", ax=ax
        )

        ax.set_title("{} across groups".format(behaviour_name))
        ax.set_ylabel("Proportion of frames")

        if ylim is not None:
            ax.set_ylim(ylim)

        if save is not None:
            plt.savefig(save)

        return_list.append(fig)

    if stat_tests:
        stat_dict = {}
        for i in combinations(treatment_dict.keys(), 2):
            # Solves issue with automatically generated examples
            if (
                beh_dict[i[0]] == beh_dict[i[1]]
                or np.var(beh_dict[i[0]]) == 0
                or np.var(beh_dict[i[1]]) == 0
            ):
                stat_dict[i] = "Identical sources. Couldn't run"
            else:
                stat_dict[i] = stats.mannwhitneyu(
                    beh_dict[i[0]], beh_dict[i[1]], alternative="two-sided"
                )
        return_list.append(stat_dict)

    return return_list


def max_behaviour(
    behaviour_dframe: pd.DataFrame, window_size: int = 10, stepped: bool = False
) -> np.array:
    """Returns the most frequent behaviour in a window of window_size frames

        Parameters:
                - behaviour_dframe (pd.DataFrame): boolean matrix containing occurrence
                of tagged behaviours per frame in the video
                - window_size (int): size of the window to use when computing
                the maximum behaviour per time slot
                - stepped (bool): sliding windows don't overlap if True. False by default

        Returns:
            - max_array (np.array): string array with the most common behaviour per instance
            of the sliding window"""

    speeds = [col for col in behaviour_dframe.columns if "speed" in col.lower()]

    behaviour_dframe = behaviour_dframe.drop(speeds, axis=1).astype("float")
    win_array = behaviour_dframe.rolling(window_size, center=True).sum()
    if stepped:
        win_array = win_array[::window_size]
    max_array = win_array[1:].idxmax(axis=1)

    return np.array(max_array)


# MACHINE LEARNING FUNCTIONS #


def gmm_compute(x: np.array, n_components: int, cv_type: str) -> list:
    """Fits a Gaussian Mixture Model to the provided data and returns evaluation metrics.

        Parameters:
            - x (numpy.array): data matrix to train the model
            - n_components (int): number of Gaussian components to use
            - cv_type (str): covariance matrix type to use.
            Must be one of "spherical", "tied", "diag", "full"

        Returns:
            - gmm_eval (list): model and associated BIC for downstream selection
    """

    gmm = mixture.GaussianMixture(
        n_components=n_components,
        covariance_type=cv_type,
        max_iter=100000,
        init_params="kmeans",
    )
    gmm.fit(x)
    gmm_eval = [gmm, gmm.bic(x)]
    return gmm_eval


def gmm_model_selection(
    x: pd.DataFrame,
    n_components_range: range,
    part_size: int,
    n_runs: int = 100,
    n_cores: int = False,
    cv_types: Tuple = ("spherical", "tied", "diag", "full"),
) -> Tuple[List[list], List[np.ndarray], Union[int, Any]]:
    """Runs GMM clustering model selection on the specified X dataframe, outputs the bic distribution per model,
       a vector with the median BICs and an object with the overall best model

        Parameters:
            - x (pandas.DataFrame): data matrix to train the models
            - n_components_range (range): generator with numbers of components to evaluate
            - n_runs (int): number of bootstraps for each model
            - part_size (int): size of bootstrap samples for each model
            - n_cores (int): number of cores to use for computation
            - cv_types (tuple): Covariance Matrices to try. All four available by default

        Returns:
            - bic (list): All recorded BIC values for all attempted parameter combinations
            (useful for plotting)
            - m_bic(list): All minimum BIC values recorded throughout the process
            (useful for plottinh)
            - best_bic_gmm (sklearn.GMM): unfitted version of the best found model
    """

    # Set the default of n_cores to the most efficient value
    if not n_cores:
        n_cores = min(multiprocessing.cpu_count(), n_runs)

    bic = []
    m_bic = []
    lowest_bic = np.inf
    best_bic_gmm = 0

    pbar = tqdm(total=len(cv_types) * len(n_components_range))

    for cv_type in cv_types:

        for n_components in n_components_range:

            res = Parallel(n_jobs=n_cores, prefer="threads")(
                delayed(gmm_compute)(
                    x.sample(part_size, replace=True), n_components, cv_type
                )
                for _ in range(n_runs)
            )
            bic.append([i[1] for i in res])

            pbar.update(1)
            m_bic.append(np.median([i[1] for i in res]))
            if m_bic[-1] < lowest_bic:
                lowest_bic = m_bic[-1]
                best_bic_gmm = res[0][0]

    return bic, m_bic, best_bic_gmm


# RESULT ANALYSIS FUNCTIONS #


def cluster_transition_matrix(
    cluster_sequence: np.array,
    nclusts: int,
    autocorrelation: bool = True,
    return_graph: bool = False,
) -> Tuple[Union[nx.Graph, Any], np.ndarray]:
    """Computes the transition matrix between clusters and the autocorrelation in the sequence.

        Parameters:
            - cluster_sequence (numpy.array):
            - nclusts (int):
            - autocorrelation (bool):
            - return_graph (bool):

        Returns:
            - trans_normed (numpy.array / networkx.Graph:
            - autocorr (numpy.array):
    """

    # Stores all possible transitions between clusters
    clusters = [str(i) for i in range(nclusts)]
    cluster_sequence = cluster_sequence.astype(str)

    trans = {t: 0 for t in product(clusters, clusters)}
    k = len(clusters)

    # Stores the cluster sequence as a string
    transtr = "".join(list(cluster_sequence))

    # Assigns to each transition the number of times it occurs in the sequence
    for t in trans.keys():
        trans[t] = len(re.findall("".join(t), transtr, overlapped=True))

    # Normalizes the counts to add up to 1 for each departing cluster
    trans_normed = np.zeros([k, k]) + 1e-5
    for t in trans.keys():
        trans_normed[int(t[0]), int(t[1])] = np.round(
            trans[t]
            / (sum({i: j for i, j in trans.items() if i[0] == t[0]}.values()) + 1e-5),
            3,
        )

    # If specified, returns the transition matrix as an nx.Graph object
    if return_graph:
        trans_normed = nx.Graph(trans_normed)

    if autocorrelation:
        cluster_sequence = list(map(int, cluster_sequence))
        autocorr = np.corrcoef(cluster_sequence[:-1], cluster_sequence[1:])
        return trans_normed, autocorr

    return trans_normed


# MAIN BEHAVIOUR TAGGING FUNCTION #


def rule_based_tagging(
    tracks: List,
    videos: List,
    coordinates: Coordinates,
    vid_index: int,
    animal_ids: List = None,
    show: bool = False,
    save: bool = False,
    fps: float = 0.0,
    speed_pause: int = 10,
    frame_limit: float = np.inf,
    recog_limit: int = 1,
    path: str = os.path.join("./"),
    arena_type: str = "circular",
    close_contact_tol: int = 15,
    side_contact_tol: int = 15,
    follow_frames: int = 20,
    follow_tol: int = 20,
    huddle_forward: int = 15,
    huddle_spine: int = 10,
    huddle_speed: int = 1,
) -> pd.DataFrame:
    """Outputs a dataframe with the registered motives per frame. If specified, produces a labeled
    video displaying the information in real time

    Parameters:
        - tracks (list): list containing experiment IDs as strings
        - videos (list): list of videos to load, in the same order as tracks
        - coordinates (deepof.preprocessing.coordinates): coordinates object containing the project information
        - vid_index (int): index in videos of the experiment to annotate
        - animal_ids (list): IDs identifying multiple animals on the arena. None if there's only one
        - show (bool): if True, enables the display of the annotated video in a separate window
        - save (bool): if True, saves the annotated video to an mp4 file
        - fps (float): frames per second of the analysed video. Same as input by default
        - speed_pause (int): size of the rolling window to use when computing speeds
        - frame_limit (float): limit the number of frames to output. Generates all annotated frames by default
        - recog_limit (int): number of frames to use for arena recognition (1 by default)
        - path (str): directory in which the experimental data is stored
        - arena_type (str): type of the arena used in the experiments. Must be one of 'circular'"
        - close_contact_tol (int): maximum distance between single bodyparts that can be used to report the trait
        - side_contact_tol (int): maximum distance between single bodyparts that can be used to report the trait
        - follow_frames (int): number of frames during which the following trait is tracked
        - follow_tol (int): maximum distance between follower and followed's path during the last follow_frames,
        in order to report a detection
        - huddle_forward (int): maximum distance between ears and forward limbs to report a huddle detection
        - huddle_spine (int): maximum average distance between spine body parts to report a huddle detection
        - huddle_speed (int): maximum speed to report a huddle detection

    Returns:
        - tag_df (pandas.DataFrame): table with traits as columns and frames as rows. Each
        value is a boolean indicating trait detection at a given time"""

    vid_name = re.findall("(.*?)_", tracks[vid_index])[0]

    coords = coordinates.get_coords()[vid_name]
    speeds = coordinates.get_coords(speed=1)[vid_name]
    arena_abs = coordinates.get_arenas[1][0]
    arena, h, w = recognize_arena(videos, vid_index, path, recog_limit, arena_type)

    # Dictionary with motives per frame
    tag_dict = {}

    if animal_ids:
        # Define behaviours that can be computed on the fly from the distance matrix
        tag_dict["nose2nose"] = smooth_boolean_array(
            close_single_contact(
                coords,
                animal_ids[0] + "_Nose",
                animal_ids[1] + "_Nose",
                close_contact_tol,
                arena_abs,
                arena[2],
            )
        )
        tag_dict[animal_ids[0] + "_nose2tail"] = smooth_boolean_array(
            close_single_contact(
                coords,
                animal_ids[0] + "_Nose",
                animal_ids[1] + "_Tail_base",
                close_contact_tol,
                arena_abs,
                arena[2],
            )
        )
        tag_dict[animal_ids[1] + "_nose2tail"] = smooth_boolean_array(
            close_single_contact(
                coords,
                animal_ids[1] + "_Nose",
                animal_ids[0] + "_Tail_base",
                close_contact_tol,
                arena_abs,
                arena[2],
            )
        )
        tag_dict["sidebyside"] = smooth_boolean_array(
            close_double_contact(
                coords,
                animal_ids[0] + "_Nose",
                animal_ids[0] + "_Tail_base",
                animal_ids[1] + "_Nose",
                animal_ids[1] + "_Tail_base",
                side_contact_tol,
                rev=False,
                arena_abs=arena_abs,
                arena_rel=arena[2],
            )
        )
        tag_dict["sidereside"] = smooth_boolean_array(
            close_double_contact(
                coords,
                animal_ids[0] + "_Nose",
                animal_ids[0] + "_Tail_base",
                animal_ids[1] + "_Nose",
                animal_ids[1] + "_Tail_base",
                side_contact_tol,
                rev=True,
                arena_abs=arena_abs,
                arena_rel=arena[2],
            )
        )
        for _id in animal_ids:
            tag_dict[_id + "_following"] = smooth_boolean_array(
                following_path(
                    coords[vid_name],
                    coords,
                    follower=_id,
                    followed=[i for i in animal_ids if i != _id][0],
                    frames=follow_frames,
                    tol=follow_tol,
                )
            )
            tag_dict[_id + "_climbing"] = smooth_boolean_array(
                pd.Series(
                    (
                        spatial.distance.cdist(
                            np.array(coords[_id + "_Nose"]), np.zeros([1, 2])
                        )
                        > (w / 200 + arena[2])
                    ).reshape(coords.shape[0]),
                    index=coords.index,
                ).astype(bool)
            )
            tag_dict[_id + "_speed"] = speeds[_id + "_speed"]
            tag_dict[_id + "_huddle"] = smooth_boolean_array(
                huddle(coords, speeds, huddle_forward, huddle_spine, huddle_speed)
            )

    else:
        tag_dict["climbing"] = smooth_boolean_array(
            pd.Series(
                (
                    spatial.distance.cdist(np.array(coords["Nose"]), np.zeros([1, 2]))
                    > (w / 200 + arena[2])
                ).reshape(coords.shape[0]),
                index=coords.index,
            ).astype(bool)
        )
        tag_dict["speed"] = speeds["Center"]
        tag_dict["huddle"] = smooth_boolean_array(
            huddle(coords, speeds, huddle_forward, huddle_spine, huddle_speed)
        )

    if any([show, save]):

        cap = cv2.VideoCapture(os.path.join(path, videos[vid_index]))
        # Keep track of the frame number, to align with the tracking data
        fnum = 0
        writer = None
        frame_speeds = {_id: -np.inf for _id in animal_ids} if animal_ids else -np.inf

        # Loop over the frames in the video
        pbar = tqdm(total=min(coords.shape[0] - recog_limit, frame_limit))
        while cap.isOpened() and fnum < frame_limit:

            ret, frame = cap.read()
            # if frame is read correctly ret is True
            if not ret:
                print("Can't receive frame (stream end?). Exiting ...")
                break

            font = cv2.FONT_HERSHEY_COMPLEX_SMALL

            # Label positions
            downleft = (int(w * 0.3 / 10), int(h / 1.05))
            downright = (int(w * 6.5 / 10), int(h / 1.05))
            upleft = (int(w * 0.3 / 10), int(h / 20))
            upright = (int(w * 6.3 / 10), int(h / 20))

            # Capture speeds
            try:
                if list(frame_speeds.values())[0] == -np.inf or fnum % speed_pause == 0:
                    for _id in animal_ids:
                        frame_speeds[_id] = speeds[_id + "_Center"][fnum]
            except AttributeError:
                if frame_speeds == -np.inf or fnum % speed_pause == 0:
                    frame_speeds = speeds["Center"][fnum]

            # Display all annotations in the output video
            if animal_ids:
                if tag_dict["nose2nose"][fnum] and not tag_dict["sidebyside"][fnum]:
                    cv2.putText(
                        frame,
                        "Nose-Nose",
                        (
                            downleft
                            if frame_speeds[animal_ids[0]] > frame_speeds[animal_ids[1]]
                            else downright
                        ),
                        font,
                        1,
                        (255, 255, 255),
                        2,
                    )
                if (
                    tag_dict[animal_ids[0] + "_nose2tail"][fnum]
                    and not tag_dict["sidereside"][fnum]
                ):
                    cv2.putText(
                        frame, "Nose-Tail", downleft, font, 1, (255, 255, 255), 2
                    )
                if (
                    tag_dict[animal_ids[1] + "_nose2tail"][fnum]
                    and not tag_dict["sidereside"][fnum]
                ):
                    cv2.putText(
                        frame, "Nose-Tail", downright, font, 1, (255, 255, 255), 2
                    )
                if tag_dict["sidebyside"][fnum]:
                    cv2.putText(
                        frame,
                        "Side-side",
                        (
                            downleft
                            if frame_speeds[animal_ids[0]] > frame_speeds[animal_ids[1]]
                            else downright
                        ),
                        font,
                        1,
                        (255, 255, 255),
                        2,
                    )
                if tag_dict["sidereside"][fnum]:
                    cv2.putText(
                        frame,
                        "Side-Rside",
                        (
                            downleft
                            if frame_speeds[animal_ids[0]] > frame_speeds[animal_ids[1]]
                            else downright
                        ),
                        font,
                        1,
                        (255, 255, 255),
                        2,
                    )
                for _id, down_pos, up_pos in zip(
                    animal_ids, [downleft, downright], [upleft, upright]
                ):
                    if tag_dict[_id + "_climbing"][fnum]:
                        cv2.putText(
                            frame, "Climbing", down_pos, font, 1, (255, 255, 255), 2
                        )
                    if (
                        tag_dict[_id + "_huddle"][fnum]
                        and not tag_dict[_id + "_climbing"][fnum]
                    ):
                        cv2.putText(
                            frame, "Huddling", down_pos, font, 1, (255, 255, 255), 2
                        )
                    if (
                        tag_dict[_id + "_following"][fnum]
                        and not tag_dict[_id + "_climbing"][fnum]
                    ):
                        cv2.putText(
                            frame,
                            "*f",
                            (int(w * 0.3 / 10), int(h / 10)),
                            font,
                            1,
                            (
                                (150, 150, 255)
                                if frame_speeds[animal_ids[0]]
                                > frame_speeds[animal_ids[1]]
                                else (150, 255, 150)
                            ),
                            2,
                        )
                    cv2.putText(
                        frame,
                        _id + ": " + str(np.round(frame_speeds[_id], 2)) + " mmpf",
                        (up_pos[0] - 20, up_pos[1]),
                        font,
                        1,
                        (
                            (150, 150, 255)
                            if frame_speeds[_id] == max(list(frame_speeds.values()))
                            else (150, 255, 150)
                        ),
                        2,
                    )

            else:
                if tag_dict["climbing"][fnum]:
                    cv2.putText(
                        frame, "Climbing", downleft, font, 1, (255, 255, 255), 2
                    )
                if tag_dict["huddle"][fnum] and not tag_dict["climbing"][fnum]:
                    cv2.putText(frame, "huddle", downleft, font, 1, (255, 255, 255), 2)
                cv2.putText(
                    frame,
                    str(np.round(frame_speeds, 2)) + " mmpf",
                    upleft,
                    font,
                    1,
                    (
                        (150, 150, 255)
                        if huddle_speed > frame_speeds
                        else (150, 255, 150)
                    ),
                    2,
                )

            if show:
                cv2.imshow("frame", frame)

                if cv2.waitKey(1) == ord("q"):
                    break

            if save:

                if writer is None:
                    # Define the codec and create VideoWriter object.The output is stored in 'outpy.avi' file.
                    # Define the FPS. Also frame size is passed.
                    writer = cv2.VideoWriter()
                    writer.open(
                        re.findall("(.*?)_", tracks[vid_index])[0] + "_tagged.avi",
                        cv2.VideoWriter_fourcc(*"MJPG"),
                        (fps if fps != 0 else cv2.CAP_PROP_FPS),
                        (frame.shape[1], frame.shape[0]),
                        True,
                    )

                print(cv2.CAP_PROP_FPS)

                writer.write(frame)

            pbar.update(1)
            fnum += 1

        cap.release()
        cv2.destroyAllWindows()

    tag_df = pd.DataFrame(tag_dict)

    return tag_df


# TODO:
#    - Add sequence plot to single_behaviour_analysis (show how the condition varies across a specified time window)
#    - Add digging to rule_based_tagging
#    - Add center to rule_based_tagging
#    - Check for features requested by Joeri
