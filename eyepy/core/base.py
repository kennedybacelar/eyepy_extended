import hashlib
import os
import warnings
from collections.abc import MutableMapping
from pathlib import Path
from typing import List, Dict, Union, Callable, Optional

import imageio
import numpy as np
from matplotlib import pyplot as plt, patches, cm, colors
from mpl_toolkits.axes_grid1 import make_axes_locatable
from skimage import transform

from eyepy.core import config
from eyepy.core.drusen import DefaultDrusenFinder, DrusenFinder
from eyepy.core.quantifier import DefaultEyeQuantifier, EyeQuantifier


class Meta(MutableMapping):

    def __init__(self, *args, **kwargs):
        self._store = dict()
        self.update(dict(*args, **kwargs))  # use the free update to set keys

    def __getitem__(self, key):
        value = self._store[key]
        if callable(value):
            self[key] = value()
        return self._store[key]

    def __setitem__(self, key, value):
        self._store[key] = value

    def __delitem__(self, key):
        del self._store[key]

    def __iter__(self):
        return iter(self._store)

    def __len__(self):
        return len(self._store)

    def __str__(self):
        return f"{os.linesep}".join(
            [f"{f}: {self[f]}" for f in self if f != "__empty"])

    def __repr__(self):
        return self.__str__()


class EnfaceImage:
    def __init__(self, data):
        self._data = data

    @property
    def data(self):
        """ Return the enface image as numpy array """
        if callable(self._data):
            self._data = self._data()
        return self._data


class Annotation(MutableMapping):

    def __init__(self, *args, **kwargs):
        self._store = dict()
        self.update(dict(*args, **kwargs))  # use the free update to set keys
        self._bscan = None

    def __getitem__(self, key):
        value = self._store[key]
        if callable(value):
            self[key] = value(self.bscan)
        return self._store[key]

    def __setitem__(self, key, value):
        self._store[key] = value

    def __delitem__(self, key):
        del self._store[key]

    def __iter__(self):
        return iter(self._store)

    def __len__(self):
        return len(self._store)

    # TODO: Make the annotation printable to get an overview
    # def __str__(self):
    #     return f"{os.linesep}".join(
    #         [f"{f}: {self[f]}" for f in self if f != "__empty"])

    # def __repr__(self):
    #     return self.__str__()

    @property
    def bscan(self):
        if self._bscan is None:
            raise AttributeError("bscan is not set for this Annotation.")
        return self._bscan

    @bscan.setter
    def bscan(self, value: "Bscan"):
        self._bscan = value


class LayerAnnotation(MutableMapping):

    def __init__(self, data, layername_mapping=None, max_height=2000):
        self._data = data
        self.max_height = max_height
        if layername_mapping is None:
            self.mapping = config.SEG_MAPPING
        else:
            self.mapping = layername_mapping

    @property
    def data(self):
        if callable(self._data):
            self._data = self._data()
        return self._data

    def __getitem__(self, key):
        data = self.data[self.mapping[key]]
        nans = np.isnan(data)
        empty = np.nonzero(np.logical_or(
            np.less(data, 0, where=~nans),
            np.greater(data, self.max_height, where=~nans)))
        data[empty] = np.nan
        if np.nansum(data) > 0:
            return data
        else:
            raise KeyError(f"There is no data given for the {key} layer")

    def __setitem__(self, key, value):
        self.data[self.mapping[key]] = value

    def __delitem__(self, key):
        self.data[self.mapping[key], :] = np.nan

    def __iter__(self):
        inv_map = {v: k for k, v in self.mapping.items()}
        return iter(inv_map.values())

    def __len__(self):
        return len(self.data.shape[0])

    def layer_indices(self, key):
        layer = self[key]
        nan_indices = np.isnan(layer)
        col_indices = np.arange(len(layer))[~nan_indices]
        row_indices = np.rint(layer).astype(int)[~nan_indices]

        return (row_indices, col_indices)


class Bscan:
    def __new__(cls, data, annotation=None, meta=None, data_processing=None,
                oct_obj=None, name=None, *args, **kwargs):

        if annotation is None:
            annotation = {"layers": None}

        def annotation_func_builder(x):
            return lambda self: self.annotation[x]

        for key in annotation:
            setattr(cls, f"_{key}", property(annotation_func_builder(key)))

        # Make all meta fields accessible as attributes of the BScan without
        # reading them. Set a property instead
        def meta_func_builder(x):
            return lambda self: self.meta[x]

        if meta is not None:
            for key in meta:
                setattr(cls, key, property(meta_func_builder(key)))
        return object.__new__(cls, *args, **kwargs)

    def __init__(self,
                 data: Union[np.ndarray, Callable],
                 annotation: Optional[Annotation] = None,
                 meta: Optional[Union[Dict, Meta]] = None,
                 data_processing: Optional[Callable] = None,
                 oct_obj: Optional["Oct"] = None,
                 name: Optional[str] = None):
        """

        Parameters
        ----------
        data : A numpy array holding the raw B-Scan data or a callable which
            returns a raw B-Scan. Raw means that it represents the unprocessed
            stored data. The actual dtype and value range depends on the storage
            format.
        annotation: Dict holding B-Scan annotations
        meta : A dictionary holding the B-Scans meta informations or
        oct_obj : Reference to the OCT Volume holding the B-Scan
        """
        self._scan_raw = data
        self._scan = None
        self._meta = meta
        self._oct_obj = oct_obj
        
        self._annotation = annotation
        if self._annotation is not None:
            self._annotation.bscan = self

        if data_processing is None:
            self._data_processing = lambda x: x
        else:
            self._data_processing = data_processing

        self._name = name

    @property
    def oct_obj(self):
        if self._oct_obj is None:
            raise AttributeError("oct_obj is not set for this Bscan object")
        return self._oct_obj

    @oct_obj.setter
    def oct_obj(self, value):
        self._oct_obj = value

    @property
    def name(self):
        if self._name is None:
            self._name = str(self.index)
        return self._name

    @property
    def index(self):
        return self.oct_obj.bscans.index(self)

    @property
    def meta(self):
        """ A dict holding all Bscan meta data """
        return self._meta

    @property
    def annotation(self):
        """ A dict holding all Bscan annotation data """
        if self._annotation is None:
            empty_layers = np.zeros((max(config.SEG_MAPPING.values()) + 1, self.shape[1]))
            l_annotation = LayerAnnotation(empty_layers)
            self._annotation = Annotation({"layers": l_annotation})
            self._annotation.bscan = self
        return self._annotation

    @property
    def scan_raw(self):
        """ An array holding a single raw B-Scan

        The dtype is not changed after the import. If available this is the
        unprocessed output of the OCT device. In any case this is the
        unprocessed data imported by eyepy.
        """
        if callable(self._scan_raw):
            self._scan_raw = self._scan_raw()
        return self._scan_raw

    @property
    def scan(self):
        """ An array holding a single B-Scan with the commonly used contrast

        The array is of dtype <ubyte> and encodes the intensities as values
        between 0 and 255.
        """
        if self._scan is None:
            self._scan = self._data_processing(self.scan_raw)
        return self._scan

    @property
    def shape(self):
        try:
            return (self.oct_obj.SizeZ, self.oct_obj.SizeX)
        except AttributeError:
            return self.scan.shape

    @property
    def layers(self):
        if callable(self._layers):
            self._layers = self._layers(self)
        return self._layers

    @property
    def drusen_raw(self):
        """ Return drusen computed from the RPE and BM layer segmentation

        The raw drusen are computed based on single B-Scans
        """
        return self._oct_obj.drusen_raw[..., self.index]

    @property
    def drusen(self):
        """ Return filtered drusen

        Drusen are filtered based on the complete volume
        """
        return self._oct_obj.drusen[..., self.index]

    def plot(self, ax=None, layers=None, drusen=False, layers_kwargs=None,
             layers_color=None, annotation_only=False, region=np.s_[...]):
        """ Plot B-Scan with segmented Layers """
        if ax is None:
            fig, ax = plt.subplots(1, 1)
        else:
            fig = plt.gcf()

        if layers is None:
            layers = []
        elif layers == "all":
            layers = config.SEG_MAPPING.keys()

        if layers_kwargs is None:
            layers_kwargs = config.layers_kwargs
        else:
            layers_kwargs = {**config.layers_kwargs, **layers_kwargs}

        if layers_color is None:
            layers_color = config.layers_color
        else:
            layers_color = {**config.layers_color, **layers_color}

        if not annotation_only:
            ax.imshow(self.scan[region], cmap="gray")
        if drusen:
            visible = np.zeros(self.drusen.shape)
            visible[self.drusen] = 1.0
            ax.imshow(self.drusen, alpha=visible, cmap="Reds")
        for layer in layers:
            color = layers_color[layer]
            try:
                segmentation = self.layers[layer]
                ax.plot(segmentation, color=color, label=layer,
                        **layers_kwargs)
            except KeyError:
                warnings.warn(f"Layer '{layer}' has no Segmentation",
                              UserWarning)


class Oct:
    """.vol header
    -----------
    All fields from the .vol header (the oct meta data) can be accessed as attributes of the
    HeyexOct object.

    SLO
    ---
    The attribute `slo` of the HeyexOct object gives access to the IR SLO image
    and returns it as a numpy.ndarray of dtype `uint8`.

    B-Scans
    -------
    Individual B-Scans can be accessed using `oct_scan[index]`. The returned
    HeyexBscan object exposes all B-Scan header fields as attributes and the
    raw B-Scan image as `numpy.ndarray` of type `float32` under the attribute
    `scan_raw`. A transformed version of the raw B-Scan which is more similar to
    the Heyex experience can be accessed with the attribute `scan` and returns
    the 4th root of the raw B-Scan scaled to [0,255] as `uint8`.

    Segmentations
    -------------
    B-Scan segmentations can be accessed for individual B-Scans like
    `bscan.segmentation`. This return a numpy.ndarray of shape (NumSeg, SizeX)
    The `segmentation` attribute of the HeyexOct object returns a dictionary,
    where the key is a number and the value is a numpy.ndarray of shape
    (NumBScans, SizeX)."""

    def __new__(cls, bscans: List[Bscan],
                localizer=None,
                meta=None, data_path=None, *args, **kwargs):
        # Set all the meta fields as attributes

        if meta is not None:
            def meta_func_builder(x):
                return lambda self: self.meta[x]

            for key in meta:
                # Every key in meta becomes a property for the new class. The
                # property calls self.meta[key] to retrieve the keys value

                # This lambda func returns a lambda func which accesses the meta
                # object for the key specified in the first lambda. Like this we
                # read the file only on access.
                setattr(cls, key, property(meta_func_builder(key)))

        return object.__new__(cls, *args, **kwargs)

    def __init__(self,
                 bscans: List[Union[Callable, Bscan]],
                 localizer: Optional[Union[Callable, EnfaceImage]] = None,
                 meta: Optional[Meta] = None,
                 drusenfinder: DrusenFinder = DefaultDrusenFinder(),
                 eyequantifier: EyeQuantifier = DefaultEyeQuantifier(),
                 data_path: Optional[str] = None):
        """

        Parameters
        ----------
        bscans :
        enfacereader :
        meta :
        drusenfinder :
        """
        self.bscans = bscans
        self._localizer = localizer
        self._enface = None
        self._meta = meta
        self._drusenfinder = drusenfinder
        self._eyequantifier = eyequantifier
        self._tform_enface_to_oct = None

        self._drusen = None
        self._drusen_raw = None
        self._layers_raw = None

        self._eyepy_id = None
        if data_path is None:
            self.data_path = Path.home() / ".eyepy"
        self.data_path = Path(data_path)
        self.drusen_path = self.data_path / ".eyepy" / f"{self.eyepy_id}_drusen_map.npy"

    def __getitem__(self, index) -> Bscan:
        """ The B-Scan at the given index"""
        x = self.bscans[index]
        if callable(x):
            self.bscans[index] = x()
        self.bscans[index].oct_obj = self
        return self.bscans[index]

    def __len__(self):
        """ The number of B-Scans """
        return len(self.bscans)

    @classmethod
    def from_heyex_xml(cls, path):
        from eyepy.io.heyex import HeyexXmlReader
        reader = HeyexXmlReader(path)
        return cls(bscans=reader.bscans,
                   localizer=reader.localizer,
                   meta=reader.oct_meta,
                   data_path=reader.path)

    @classmethod
    def from_heyex_vol(cls, path):
        from eyepy.io.heyex import HeyexVolReader
        reader = HeyexVolReader(path)
        return cls(bscans=reader.bscans,
                   localizer=reader.localizer,
                   meta=reader.oct_meta,
                   data_path=Path(path).parent)

    @classmethod
    def from_folder(cls, path):
        path = Path(path)
        img_paths = sorted(list(path.iterdir()))

        def read_func(p):
            return lambda: imageio.imread(p)

        bscans = [lambda: Bscan(read_func(p), name=p.name) for p in img_paths]
        return cls(bscans=bscans, data_path=path)

    def estimate_bscan_distance(self):
        # Try to estimate B-Scan distances. Can be used if Bscan Positions
        # but not their distance is in the meta information

        # Pythagoras in case B-Scans are rotated with respect to the enface
        a = self[-1].StartY - self[0].StartY
        b = self[-1].StartX - self[0].StartX
        self.meta["Distance"] = np.sqrt(a ** 2 + b ** 2) / (
                len(self) - 1)
        return self.Distance

    @property
    def eyepy_id(self):
        """ Visit ID for saving visit related files """
        if self._eyepy_id is None:
            # Compute a hash of the first B-Scan as ID
            sha1 = hashlib.sha1()
            sha1.update(self[0].scan.tobytes())
            self._eyepy_id = sha1.hexdigest()
        return self._eyepy_id

    @property
    def shape(self):
        return (self.SizeZ, self.SizeX, self.NumBScans)
    
    @property
    def SizeZ(self):
        try:
            return self.meta["SizeZ"]
        except (AttributeError, KeyError, TypeError):
            return self[0].scan.shape[0]
    
    @property    
    def SizeX(self):
        try:
            return self.meta["SizeX"]
        except (AttributeError, KeyError, TypeError):
            return self[0].scan.shape[1]

    @property    
    def NumBScans(self):
        try:
            return self.meta["NumBScans"]
        except (AttributeError, KeyError, TypeError):
            return len(self)

    @property
    def enface(self):
        """ A numpy array holding the OCTs localizer enface if available """
        try:
            return self._localizer.data
        except AttributeError:
            raise AttributeError("This OCT object has not localizer image.")

    @property
    def volume_raw(self):
        """ An array holding the OCT volume

        The dtype is not changed after the import. If available this is the
        unprocessed output of the OCT device. In any case this is the
        unprocessed data imported by eyepy.
        """
        return np.stack([x.scan_raw for x in self.bscans], axis=-1)

    @property
    def volume(self):
        """ An array holding the OCT volume with the commonly used contrast

        The array is of dtype <ubyte> and encodes the intensities as values
        between 0 and 255.
        """
        return np.stack([x.scan for x in self.bscans], axis=-1)

    @property
    def layers_raw(self):
        """ Height maps for all layers combined into one volume.

        Layers for all B-Scans are stacked such that we get a volume L x B x W
        where L are different Layers, B are the B-Scans and W is the Width of
        the B-Scans.
        """
        if self._layers_raw is None:
            self._layers_raw = np.stack([x.layers.data
                                         for x in self], axis=1)
        return self._layers_raw

    @property
    def layers(self):
        """ Height maps for all layers accessible by the layers name """
        nans = np.isnan(self.layers_raw)
        empty = np.nonzero(np.logical_or(
            np.less(self.layers_raw, 0, where=~nans),
            np.greater(self.layers_raw, self.SizeZ, where=~nans)))

        data = self.layers_raw.copy()
        data[empty] = np.nan
        return {name: data[i, ...] for name, i in config.SEG_MAPPING.items()
                if np.nansum(data[i, ...]) != 0}

    @property
    def meta(self):
        """ A dict holding all OCT meta data

        The object can be printed to see all available meta data.
        """
        return self._meta

    @property
    def drusen(self):
        """ Final drusen after post processing the initial raw drusen

        Here the `filter` function of the DrusenFinder has been applied
        """
        if self._drusen is None:
            # Try to load the drusen from the default location
            try:
                self._drusen = np.load(self.drusen_path)
            except (NotADirectoryError, FileNotFoundError):
                self._drusen = self._drusenfinder.filter(self.drusen_raw)
                self.drusen_path.parent.mkdir(parents=True, exist_ok=True)
                np.save(self.drusen_path, self._drusen)
        return self._drusen

    def drusen_recompute(self, drusenfinder=None):
        """ Recompute Drusen optionally with a custom DrusenFinder

        Use this if you do not like the computed / loaded drusen
        """
        if drusenfinder is not None:
            self._drusenfinder = drusenfinder

        self._drusen_raw = self._drusenfinder.find(self)
        self._drusen = self._drusenfinder.filter(self.drusen_raw)
        np.save(self.drusen_path, self._drusen)
        return self._drusen

    @property
    def drusen_raw(self):
        """ Initial drusen before post procssing

        The initial drusen found by the DrusenFinders `find` function.
        """
        if self._drusen_raw is None:
            self._drusen_raw = self._drusenfinder.find(self)
        return self._drusen_raw

    @property
    def quantification(self):
        return self._eyequantifier.quantify(self)

    @property
    def tform_enface_to_oct(self):
        if self._tform_enface_to_oct is None:
            self._tform_enface_to_oct = self._estimate_enface_to_oct_tform()
        return self._tform_enface_to_oct

    @property
    def tform_oct_to_enface(self):
        return self.tform_enface_to_oct.inverse
    
    @property
    def enface_shape(self):
        try:
            return self.enface.shape
        except:
            return (self.SizeX, self.SizeX)

    def _estimate_enface_to_oct_tform(self):
        oct_projection_shape = (self.NumBScans, self.SizeX)
        src = np.array(
            [oct_projection_shape[0] - 1, 0,  # Top left
             oct_projection_shape[0] - 1, oct_projection_shape[1] - 1,  # Top right
             0, 0,  # Bottom left
             0, oct_projection_shape[1] - 1  # Bottom right
             ]).reshape((-1, 2))

        try:
            # Try to map the oct projection to the enface image
            dst = np.array(
                [self[-1].StartY / self.ScaleXSlo, self[-1].StartX / self.ScaleYSlo,
                 self[-1].EndY / self.ScaleXSlo, self[-1].EndX / self.ScaleYSlo,
                 self[0].StartY / self.ScaleXSlo, self[0].StartX / self.ScaleYSlo,
                 self[0].EndY / self.ScaleXSlo, self[0].EndX / self.ScaleYSlo
                 ]).reshape((-1, 2))
        except AttributeError:
            # Map the oct projection to a square area of shape (bscan_width, bscan_width)
            warnings.warn(f"Bscan positions on enface image or the scale of the "
                          f"enface image is missing. We assume that the B-Scans cover "
                          f"a square area and are equally spaced.",
                          UserWarning)
            b_width = self[0].shape[1]
            dst = np.array(
                [b_width - 1, 0,  # Top left
                 b_width - 1, b_width - 1,  # Top right
                 0, 0,  # Bottom left
                 0, b_width - 1  # Bottom right
                 ]).reshape((-1, 2))

        src = src[:, [1, 0]]
        dst = dst[:, [1, 0]]
        tform = transform.estimate_transform("affine", src, dst)

        if not np.allclose(tform.inverse(tform(src)), src):
            msg = f"Problem with transformation of OCT Projection to the enface image space."
            raise ValueError(msg)

        return tform

    @property
    def drusen_projection(self):
        return np.swapaxes(np.sum(self.drusen, axis=0), 0, 1)

    @property
    def drusen_enface(self):
        """ Drusen projection warped into the enface space """
        return transform.warp(self.drusen_projection.astype(float),
                              self.tform_oct_to_enface,
                              output_shape=self.enface_shape)

    @property
    def drusenfinder(self):
        """ Get and set the DrusenFinder object

        When the DrusenFinder object is set all drusen are removed.
        """
        return self._drusenfinder

    @drusenfinder.setter
    def drusenfinder(self, drusenfinder):
        self._drusen = None
        self._drusen_raw = None
        self._drusenfinder = drusenfinder

    def plot(self, ax=None, enface=True, drusen=False, bscan_region=False,
             bscan_positions=None, masks=False, region=np.s_[...], alpha=1):
        """

        Parameters
        ----------
        ax :
        slo :
        drusen :
        bscan_region :
        bscan_positions :
        masks :
        region : slice object
        alpha :

        Returns
        -------

        """

        if ax is None:
            ax = plt.gca()

        if enface:
            self.plot_enface(ax=ax, region=region)
        if drusen:
            self.plot_drusen(ax=ax, region=region, alpha=alpha)
        if bscan_positions is not None:
            self.plot_bscan_positions(ax=ax, bscan_positions=bscan_positions,
                                      region=region,
                                      line_kwargs={"linewidth": 0.5,
                                                   "color": "green"})
        if bscan_region:
            self.plot_bscan_region(region=region, ax=ax)

        if masks:
            self.plot_masks(region=region, ax=ax)
        # if quantification:
        #    self.plot_quantification(space=space, region=region, ax=ax,
        #    q_kwargs=q_kwargs)

    def plot_layer_distance(self, region=np.s_[...], ax=None, top_layer="RPE", bot_layer="BM"):
        if ax is None:
            ax = plt.gca()
        
        bot = self.layers[bot_layer]
        top = self.layers[top_layer]

        distance = bot-top
        img = transform.warp(distance.astype(float),
                             self.tform_oct_to_enface,
                             output_shape=self.enface_shape)
        ax.imshow(img[region], cmap="gray")
        
        

    def plot_masks(self, region=np.s_[...], ax=None, color="r", linewidth=0.5):
        """

        Parameters
        ----------
        region :
        ax :
        color :
        linewidth :

        Returns
        -------

        """
        primitives = self._eyequantifier.plot_primitives(self)
        if ax is None:
            ax = plt.gca()

        for circle in primitives["circles"]:
            c = patches.Circle(circle["center"], circle["radius"],
                               facecolor='none',
                               edgecolor=color, linewidth=linewidth)
            ax.add_patch(c)

        for line in primitives["lines"]:
            x = [line["start"][0], line["end"][0]]
            y = [line["start"][1], line["end"][1]]
            ax.plot(x, y, color=color, linewidth=linewidth)

    def plot_enface(self, ax=None, region=np.s_[...]):
        if ax is None:
            ax = plt.gca()
        ax.imshow(self.enface[region], cmap="gray")

    def plot_bscan_positions(self, bscan_positions="all", ax=None,
                             region=np.s_[...], line_kwargs=None):
        if bscan_positions is None:
            bscan_positions = []
        elif bscan_positions == "all" or bscan_positions is True:
            bscan_positions = range(0, len(self))

        if line_kwargs is None:
            line_kwargs = config.line_kwargs
        else:
            line_kwargs = {**config.line_kwargs, **line_kwargs}

        for i in bscan_positions:
            bscan = self[i]
            x = np.array([bscan.StartX, bscan.EndX]) / self.ScaleXSlo
            y = np.array([bscan.StartY, bscan.EndY]) / self.ScaleYSlo

            ax.plot(x, y, **line_kwargs)

    def plot_bscan_region(self, region=np.s_[...], ax=None):

        if ax is None:
            ax = plt.gca()

        up_right_corner = (self[-1].EndX / self.ScaleXSlo,
                           self[-1].EndY / self.ScaleYSlo)
        width = (self[0].StartX - self[0].EndX) / self.ScaleXSlo
        height = (self[0].StartY - self[-1].EndY) / self.ScaleYSlo
        # Create a Rectangle patch
        rect = patches.Rectangle(up_right_corner, width, height, linewidth=1,
                                 edgecolor='r', facecolor='none')

        # Add the patch to the Axes
        ax.add_patch(rect)

    def plot_drusen(self, ax=None, region=np.s_[...], cmap="Reds",
                    vmin=None, vmax=None, cbar=True, alpha=1):
        drusen = self.drusen_enface

        if ax is None:
            ax = plt.gca()

        if vmax is None:
            vmax = drusen.max()
        if vmin is None:
            vmin = 1

        visible = np.zeros(drusen[region].shape)
        visible[np.logical_and(vmin < drusen[region],
                               drusen[region] < vmax)] = 1

        if cbar:
            divider = make_axes_locatable(ax)
            cax = divider.append_axes("right", size="5%", pad=0.05)
            plt.colorbar(
                cm.ScalarMappable(colors.Normalize(vmin=vmin, vmax=vmax),
                                  cmap=cmap), cax=cax)

        ax.imshow(drusen[region], alpha=visible * alpha, cmap=cmap, vmin=vmin,
                  vmax=vmax)

    def plot_enface_bscan(self, ax=None, n_bscan=0):
        """ Plot Slo with one selected B-Scan """
        raise NotImplementedError()

    def plot_bscans(self, bs_range=range(0, 8), cols=4, layers=None,
                    layers_kwargs=None):
        """ Plot a grid with B-Scans """
        rows = int(np.ceil(len(bs_range) / cols))
        if layers is None:
            layers = []

        fig, axes = plt.subplots(
            cols, rows, figsize=(rows * 4, cols * 4))

        with np.errstate(invalid='ignore'):
            for i in bs_range:
                bscan = self[i]
                ax = axes.flatten()[i]
                bscan.plot(ax=ax, layers=layers, layers_kwargs=layers_kwargs)
