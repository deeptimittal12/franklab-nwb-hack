import copy
import numpy as np
from abc import ABC, abstractmethod
from scipy.interpolate import interp1d
import intervals as iv


class TimeIntervals():
    """
    Represent a list of non-overlapping time intervals.
    
    Currently uses python-intervals as a backend, abstracting this from other classes that use intervals. 
    To replace with a different backend, change the methods accordingly.
    """
    
    def __init__(self, bounds=None):
        self.intervals = self.__make_intervals(bounds)
        
    def __make_intervals(self, bounds):
        '''Create an interval.Interval from start/end times'''
        if bounds is None:
            return iv.empty()
        if isinstance(bounds, iv.Interval):
            return bounds
        if isinstance(bounds, iv.AtomicInterval):
            return iv.Interval(bounds) # converts AtomicInterval to Interval
        elif (isinstance(bounds, np.ndarray) and bounds.ndim == 2 and bounds.shape[1] == 2):
            intervals = iv.empty()
            for ivl in bounds:
                intervals = intervals | iv.closed(*ivl)
            return intervals
        else:
            raise TypeError("'bounds' must be an intervals.Interval, intervals.AtomicInterval, or an m x 2 numpy array")
        
    def to_array(self):
        '''Create m x 2 numpy array from the set of intervals in an TimeIntervals object'''
        return np.array([[atomic_ivl.lower, atomic_ivl.upper] for atomic_ivl in self.intervals])
    
    def durations(self):
        '''Return duration of each obs_interval'''
        return np.diff(self.to_array(), axis=1)
        
    def __and__(self, time_intervals):
        '''Return the intersection of two TimeIntervals'''
        return TimeIntervals(self.intervals & time_intervals.intervals)

    def intersect(self, time_intervals):
        '''Return the intersection of two TimeIntervals'''
        return self & time_intervals
    
    def __or__(self, time_intervals):
        '''Return the union of two TimeIntervals'''
        return TimeIntervals(self.intervals | time_intervals.intervals)

    def union(self, time_intervals):
        '''Return the union of two TimeIntervals'''
        return self | time_intervals

    def __contains__(self, t):
        """Check whether time t is in TimeIntervals. Supports the 'v in TimeIntervals' pattern."""
        return t in self.intervals
    
    def __len__(self):
        """Return number of non-overlapping intervals (i.e. start/stop) in this TimeIntervals."""
        if self.intervals.is_empty(): # iv.empty() has len 1; it contains a special empty interval (I.inf, -I.inf)
            return 0
        return len(self.intervals)

    

    
    
class Data(ABC):
    """Abstract base class for representing data observed over some observation intervals.
    
    In addition to 
    
    """
    @abstractmethod
    def __init__(self, obs_intervals):
        if not(isinstance(obs_intervals, TimeIntervals)):
            raise TypeError("'obs_intervals' must be of type nwb_query.TimeIntervals")
        self.obs_intervals = obs_intervals
        
    # Decorate as an abstractmethod to force all Data subclasses to implement time_query
#     @abstractmethod     
    def time_query(self, query):
        raise NotImplementedError("Subclasses of abstract class Data must implement 'time_query' method.")

        
        
class PointData(Data):
    '''
    Represent a point process, a list of discrete event times occurring during defined intervals,
    optionally with mark data associated with each event.
    '''    
    def __init__(self, event_times, obs_intervals, marks=None):
        
        super(PointData, self).__init__(obs_intervals)
        
        if not isinstance(event_times, np.ndarray):
            raise TypeError("'event_times' must be a numpy.array")
        if not(event_times.ndim==1):
            raise ValueError("'event_times' must be a vector (1-dimensional array).")
        if marks and not isinstance(marks, np.ndarray):
            raise TypeError("'marks' must be a numpy.array")
        if marks and not(marks.shape[0]==self.event_times.shape[0]):
            raise ValueError("'marks' must have same # of entries (rows) as 'event_times'.")        
        self.event_times = event_times
        self.obs_intervals = obs_intervals
        self.marks = marks
    
    def time_query(self, query_intervals):
        '''Return PointData with data available during requested time_interval.'''
        
        # Find the resulting obs_intervals where the data have support (i.e. intersect with the selection intervals)
        if isinstance(query_intervals, EventData):
            result_obs_intervals = self.obs_intervals & query_intervals.select_intervals
        elif isinstance(query_intervals, TimeIntervals):
            result_obs_intervals = self.obs_intervals & query_intervals
        else:
            raise TypeError("'query_intervals' must be of type nwb_query.EventData or nwb_query.TimeIntervals")
            
        # Collect the valid event_times in the query
        result_event_times = np.array([t for t in self.event_times if t in query_intervals])
        return PointData(event_times=result_event_times,
                            obs_intervals=result_obs_intervals)
        
        
    def mark_with_ContinuousData(self, continuous_data, merge_obs_intervals=True, interpolation='linear'):
        """
        Evaluate ContinuousData at each event_time and add result as a corresponding mark. 
        
        If merge_obs_intervals=True, the resulting PointData will have obs_intervals that 
        are the intersection of the obs_intervals of the inputs, and it may not contain all 
        of the original event_times. Otherwise, all event_times will be returned, but will be
        marked with 'None' at times when continuous_data is undefined (i.e. outside its 
        obs_intervals).
        
        'interpolation' is passed to scipy.interp1d as 'kind'. Available kinds include ('nearest', 'linear'
        'quadratic', 'cubic')
        
        """
        
        if continuous_data.data.ndim > 1 and not (interpolation == 'nearest' or interpolation == 'linear'):
            raise NotImplementedError("For data > 1-D, only 'nearest' and 'linear' interpolation are currently suppported")

        # Make an interpolation function using the continuous data and timestamps, which we will use to
        # evaluate the ContinuousData at the event_times of this PointData
        interpolator = interp1d(x=continuous_data.timestamps, 
                                y=continuous_data.data, 
                                kind=interpolation, 
                                axis=0)
        
        mark_shape = continuous_data.data.shape[1:] # 1 row per event, but mark data can be multi-d
        
        if merge_obs_intervals:
            # timequery on pp: intersects obs_intervals and discards event_times outside overlapping region
            result_pp = self.time_query(continuous_data.obs_intervals)
            # don't need to worry about events outside of the continuous data, b/c we have already filtered event_times
            result_pp.marks = interpolator(result_pp.event_times)
            return result_pp
        else:
            result_marks = []
            for event_t in self.event_times:
                if event_t in continuous_data.obs_intervals:
                    result_marks.append(interpolator(event_t), 'extrapolate') # extrapolate when within obs_intervals but outside last sample?
                else:
                    result_marks.append(np.full(mark_shape, None))  # set mark to None if the event occurs outside the continuous data                             
            return PointData(self.event_times, self.obs_intervals,
                                     marks=np.concatenate(result_marks,axis=0))

class ContinuousData(Data):
    
    def __init__(self, data, timestamps, obs_intervals=None, find_gaps=False):
        if not obs_intervals:
            if find_gaps:
                obs_intervals = self.__find_obs_intervals(self.timestamps)
            else:
                timestamps_range = np.array([[timestamps[0], timestamps[-1]]])
                obs_intervals = TimeIntervals(timestamps_range)
        super(ContinuousData, self).__init__(obs_intervals)
        self.data = data
        self.timestamps = timestamps
    
    def __find_obs_intervals(self, timestamps, gap_threshold_samps=1.5):
        """Optionally build obs_intervals from any gaps in the data.
        
        This is currently not tested.
        """
        import warnings
        warnings.warn("Deducing obs_interval is currently untested, may be bogus.")
        stepsize = np.mean(np.diff(timestamps, 1)) # use first derivatives to estimate the stepsize
        diffs = np.diff(timestamps, 2)  # use second derivative to identify gaps
        epsilon = gap_threshold_samps * stepsize  # only count if the gap is big with respect to the stepsize
        ivl_end_indices = np.where(diffs > epsilon)[0] + 1  
        if ivl_end_indices.size == 0:  # no gaps in observation
            return TimeIntervals(np.array([[timestamps[0], timestamps[-1]]]))
        else:
            # append the last valid index of the array to the end indices
            np.append(ivl_end_indices, ivl_end_indices.size-1) 
            # build the obs_intervals
            bounds = []  
            for i, end_idx in enumerate(ivl_end_indices):
                if i == 0:   # handle the first interval
                    bounds.append([self.timestamps[0], self.timestamps[end_idx]])
                else:
                    previous_end_idx = ivl_end_indices[i-1]
                    new_start_idx = previous_end_idx + 1
                    bounds.append([self.timestamps[new_start_idx], self.timestamps[end_idx]])
            return TimeIntervals(np.array(bounds))
            

    def time_query(self, query_intervals):
        """Return ContinuousData using the specified time_intervals.
        
        The resulting obs_intervals is the intersection of the obs_intervals of this ContinuousData and
        the provided. time_intevals. The resulting data and timestamps are those occurring in the 
        resulting obs_intervals.
        """
        # Constrain the resulting obs_intervals to where the data have support (i.e. intersect with selection intervals)
        if isinstance(query_intervals, EventData):
            result_obs_intervals = self.obs_intervals & query_intervals.select_intervals
        elif isinstance(query_intervals, TimeIntervals):
            result_obs_intervals = self.obs_intervals & query_intervals
        else:
            raise TypeError("'query_intervals' must be of type nwb_query.EventData or nwb_query.TimeIntervals")
        
        # Get index into data and timestamps of interval starts/ends
        result_bounds = result_obs_intervals.to_array()
        result_lower_bounds = result_bounds[:,0]
        result_upper_bounds = result_bounds[:,1]
        # Intervals are closed; find first/last matching timestamps for lower/upper bounds
        result_lower_index = np.searchsorted(self.timestamps, result_lower_bounds, side='left')
        result_upper_index = np.searchsorted(self.timestamps, result_upper_bounds, side='right')

        # TODO: speedup by initializing output arrays (use index to compute size)
        result_data = []
        result_timestamps = []
        for idx_lower, idx_upper in zip(result_lower_index, result_upper_index):
            result_data.append(self.data[idx_lower:idx_upper,:])
            result_timestamps.append(self.timestamps[idx_lower:idx_upper])
        
        return ContinuousData(data=np.concatenate(result_data),
                              timestamps=np.concatenate(result_timestamps),
                              obs_intervals=result_obs_intervals)
    
    
    def filter_intervals(self, func, data_cols=False):
        """Return a EventData where the ContinuousData fulfills a boolean lambda function ('func').
        
        By default, 'func' should accept all columns of ContinuousData.data as input.
        Otherwise, provide a list of the column indices that should be used.
        """
        if self.data.shape[0] == 0:
            return EventData(select_intervals=TimeIntervals(),
                                 obs_intervals=self.obs_intervals)
                                 
        # apply the function to the correct columns of the data
        if data_cols:
            assert max(data_cols) < self.data.shape[1]
            func_of_data = func(self.data[:, data_cols])
        else:
            func_of_data = func(self.data)
        
        # Get the up/down crossing indices, i.e. the first/last elements in each interval that fulfill 'func'
        assert func_of_data.dtype == 'bool'
        df = np.diff(func_of_data.astype(np.int8))
        up_crossings = np.where(df == 1)[0] + 1
        down_crossings = np.where(df == -1)[0]

        # if data begins while function is true, include this as an up-crossing
        if func_of_data[0]:
            up_crossings = np.insert(up_crossings, 0, 0)

        # if data ends while function is true, include this as a down-crossing    
        if func_of_data[-1]:
            down_crossings = np.append(down_crossings, func_of_data.shape[0]-1)
        
        # Create the time intervals
        up_times = self.timestamps[up_crossings]
        down_times = self.timestamps[down_crossings]
        interval_bounds = np.array((up_times, down_times)).T
        filtered_intervals = TimeIntervals(interval_bounds)
        
        # Return a EventData instance, with the same obs_intervals as this ContinuousData instance
        return EventData(select_intervals=filtered_intervals,
                             obs_intervals=self.obs_intervals)
        

    

class EventData(Data):
    """Represent events occurring during a period of observation.
    
    
    A EventData object can be used for querying additional datasets, while matinating the 
    correct observation intervals from the initial dataset. For example, selecting time intervals 
    where an animal was running faster than a threshold speed, and using the resulting 
    EventData object to query spiking data for a clustered unit. The resulting 
    observation intervals should be the intersection of the speed EventData and the 
    spiking PointData obs_intervals.  
    """
    
    def __init__(self, select_intervals, obs_intervals):
        super(EventData, self).__init__(obs_intervals)
        if not isinstance(select_intervals, TimeIntervals):
            raise TypeError("'select_intervals' must be a query.TimeIntervals instance")
        if not np.all([s_ivl in obs_intervals for s_ivl in select_intervals.intervals]):
            raise ValueError("'select_intervals' must be fully contained in 'obs_intervals'.")
        self.select_intervals = select_intervals
        self.obs_intervals = obs_intervals
    
    
    def __contains__(self, t):
        """Check whether time t is in the EventData's selection intervals."""
        return t in self.select_intervals
    
    def supports(self, t):
        """Check whether time t is in the EventData's observation intervals.
        (i.e. Returns true if there is support for data at this time, regardless of
        whether t is in the selection interval.)
        """
        return t in self.obs_intervals
    
    def durations(self):
        """Durations of the selection intervals."""
        return self.select_intervals.durations()
    
    def obs_durations(self):
        """Durations of the observation intervals."""
        return self.obs_intervals.durations()
    
    def __and__(self, other):
        '''Return the intersection of two EventDatas'''
        if not isinstance(other, EventData):
            raise TypeError("'other' must be a nwb_query.EventData object")
        result_select_ivl = self.select_intervals & other.select_intervals
        result_obs_ivl = self.obs_intervals & other.obs_intervals
        return EventData(result_select_ivl, result_obs_ivl)

    def intersect(self, other):
        '''Return the intersection of two EventDatas'''
        return self & other
    
    def __or__(self, other):
        '''Return the union of two EventDatas'''
        if not isinstance(other, EventData):
            raise TypeError("'other' must be a nwb_query.EventData object")
        result_select_ivl = self.select_intervals | other.select_intervals
        result_obs_ivl = self.obs_intervals | other.obs_intervals
        return EventData(result_select_ivl, result_obs_ivl)

    def union(self, other):
        '''Return the union of two EventDatas'''
        return self | other

        