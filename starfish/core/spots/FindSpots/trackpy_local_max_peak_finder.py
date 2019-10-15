import warnings
from functools import partial
from typing import Optional, Tuple, Union

import numpy as np
import xarray as xr
from trackpy import locate

from starfish.core.image.Filter.util import determine_axes_to_group_by
from starfish.core.imagestack.imagestack import ImageStack
from starfish.core.spots.FindSpots import spot_finding_utils
from starfish.core.types import Axes, SpotAttributes, SpotFindingResults
from ._base import FindSpotsAlgorithm


class TrackpyLocalMaxPeakFinder(FindSpotsAlgorithm):
    """
    Find spots using a local max peak finding algorithm

    This is a wrapper for :code:`trackpy.locate`, which implements a version of the
    `Crocker-Grier <crocker_grier>`_ algorithm.

    .. _crocker_grier: https://physics.nyu.edu/grierlab/methods3c/

    Parameters
    ----------

    spot_diameter :
        odd integer or tuple of odd integers.
        This may be a single number or a tuple giving the feature’s extent in each dimension,
        useful when the dimensions do not have equal resolution (e.g. confocal microscopy).
        The tuple order is the same as the image shape, conventionally (z, y, x) or (y, x).
        The number(s) must be odd integers. When in doubt, round up.
    min_mass : Optional[float]
        The minimum integrated brightness. This is a crucial parameter for eliminating spurious
        features. Recommended minimum values are 100 for integer images and 1 for float images.
        Defaults to 0 (no filtering).
    max_size : float
        maximum radius-of-gyration of brightness, default None
    separation : Union[float, tuple]
        Minimum separation between features. Default is diameter + 1. May be a tuple, see
        diameter for details.
    percentile : float
        Features must have a peak brighter than pixels in this percentile. This helps eliminate
        spurious peaks. (default = 0)
    noise_size : Union[float, tuple]
        Width of Gaussian blurring kernel, in pixels Default is 1. May be a tuple, see diameter
        for details.
    smoothing_size : Union[float, tuple]
        The size of the sides of the square kernel used in boxcar (rolling average) smoothing,
        in pixels Default is diameter. May be a tuple, making the kernel rectangular.
    threshold : float
        Clip bandpass result below this value. Thresholding is done on the already
        background-subtracted image. By default, 1 for integer images and 1/255 for float
        images.
    measurement_type : str ['max', 'mean']
        name of the function used to calculate the intensity for each identified spot area
        (default = max)
    preprocess : boolean
        Set to False to turn off bandpass prepossessing.
    max_iterations : integer
        Max number of loops to refine the center of mass, (default = 10)
    is_volume : bool
        if True, run the algorithm on 3d volumes of the provided stack (default = False)
    verbose : bool
        If True, report the percentage completed (default = False) during processing

    Notes
    -----
    See also trackpy locate: http://soft-matter.github.io/trackpy/dev/generated/trackpy.locate.html

    """

    def __init__(
            self, spot_diameter, min_mass, max_size, separation, percentile=0,
            noise_size: Tuple[int, int, int] = (1, 1, 1), smoothing_size=None, threshold=None,
            preprocess: bool = False, max_iterations: int = 10, measurement_type: str = 'max',
            is_volume: bool = False, verbose=False, radius_is_gyration: bool = False) -> None:

        self.diameter = spot_diameter
        self.minmass = min_mass
        self.maxsize = max_size
        self.separation = separation
        self.noise_size = noise_size
        self.smoothing_size = smoothing_size
        self.percentile = percentile
        self.threshold = threshold
        self.measurement_function = self._get_measurement_function(measurement_type)
        self.preprocess = preprocess
        self.max_iterations = max_iterations
        self.is_volume = is_volume
        self.verbose = verbose
        self.radius_is_gyration = radius_is_gyration

    def image_to_spots(self, data_image: Union[np.ndarray, xr.DataArray]) -> SpotAttributes:
        """

        Parameters
        ----------
        data_image : np.ndarray
            three-dimensional image containing spots to be detected

        Returns
        -------
        SpotAttributes :
            spot attributes table for all detected spots

        """
        data_image = np.asarray(data_image)
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', FutureWarning)  # trackpy numpy indexing warning
            warnings.simplefilter('ignore', UserWarning)  # yielded if black images
            attributes = locate(
                data_image,
                diameter=self.diameter,
                minmass=self.minmass,
                maxsize=self.maxsize,
                separation=self.separation,
                noise_size=self.noise_size,
                smoothing_size=self.smoothing_size,
                threshold=self.threshold,
                percentile=self.percentile,
                preprocess=self.preprocess,
                max_iterations=self.max_iterations,
            )

        # when zero spots are detected, 'ep' is missing from the trackpy locate results.
        if attributes.shape[0] == 0:
            attributes['ep'] = []

        # TODO ambrosejcarr: data should always be at least pseudo-3d, this may not be necessary
        # TODO ambrosejcarr: this is where max vs. sum vs. mean would be parametrized.
        # here, total_intensity = sum, intensity = max
        new_colnames = [
            'y', 'x', 'total_intensity', 'radius', 'eccentricity', 'intensity', 'raw_mass', 'ep'
        ]
        if len(data_image.shape) == 3:
            attributes.columns = ['z'] + new_colnames
        else:
            attributes.columns = new_colnames

        attributes['spot_id'] = np.arange(attributes.shape[0])
        return SpotAttributes(attributes)

    def run(
            self,
            image_stack: ImageStack,
            reference_image: Optional[ImageStack] = None,
            n_processes: Optional[int] = None,
            *args,
    ) -> SpotFindingResults:
        """
        Find spots.

        Parameters
        ----------
        image_stack : ImageStack
            ImageStack where we find the spots in.
        reference_image : xr.DataArray
            (Optional) a reference image. If provided, spots will be found in this image, and then
            the locations that correspond to these spots will be measured across each channel.
        n_processes : Optional[int] = None,
            Number of processes to devote to spot finding.
        """
        spot_finding_method = partial(self.image_to_spots, *args)
        if reference_image:
            data_image = reference_image._squeezed_numpy(*{Axes.ROUND, Axes.CH})
            reference_spots = spot_finding_method(data_image)
            results = spot_finding_utils.measure_intensities_at_spot_locations_across_imagestack(
                image_stack,
                reference_spots,
                measurement_function=self.measurement_function,
                radius_is_gyration=self.radius_is_gyration)
        else:
            spot_attributes_list = image_stack.transform(
                func=spot_finding_method,
                group_by=determine_axes_to_group_by(self.is_volume),
                n_processes=n_processes
            )
            results = SpotFindingResults(imagestack_coords=image_stack.xarray.coords,
                                         log=image_stack.log,
                                         spot_attributes_list=spot_attributes_list)
        return results
