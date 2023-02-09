# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, unicode_literals

import io

import numpy as np
import six

from .image import NanoscopeImage
from .parameter import parse_parameter
from .error import UnsupportedVersion, MissingImageData


def read(f, encoding='cp1252', header_only=False, check_version=True):
    """
    Reads the specified file, given as either a filename or an already opened
    file object. Passed file objects must be opened in binary mode. Meant as the
    typical entry point for loading in afm data.

    :param f: Filename of the file to read or an opened file object. File
              objects must be opened in binary mode.
    :param encoding: The encoding to use when reading the file header. Defaults
                     to cp1252.
    :param header_only: Whether to read only the header of the file. Defaults to
                        False.
    :param check_version: Whether to enforce version checking for known
                          supported versions. Defaults to True.
    :returns: A NanoscopeFile object containing the image data.
    :raises OSError: If a passed file object is not opened in binary mode.
    """
    # 2023年2月8日  精简代码
    with io.open(f, 'rb') as file_obj:
        assert 'b' in file_obj.mode, 'File must be opened in binary mode.'
        images = NanoscopeFile(file_obj, encoding, header_only, check_version)
    return images


class NanoscopeFile(object):
    """
    Handles reading and parsing Nanoscope files.
    """
    supported_versions = ['0x05120000', '0x05120130', '0x09300201', '0x09010300', '0x09700105']

    def __init__(self, file_object, encoding='utf-8', header_only=False, check_version=True):
        self.images = {}
        self.config = {'_Images': {}}
        self.encoding = encoding

        self._read_header(file_object, check_version)
        if not header_only:
            for image_type in six.iterkeys(self.config['_Images']):
                # 2023年2月8日 修改
                try:
                    self._read_image_data(file_object, image_type)
                except:
                    # print("Failed to read image_type:", image_type)
                    # 有些 image_type 读取不了
                    pass

    @property
    def height(self):
        """
        Return the height image if it exists, else ``None``.
        """
        return self.image('Height')

    @property
    def amplitude(self):
        """
        Return the amplitude image if it exists, else ``None``.
        """
        return self.image('Amplitude')

    @property
    def phase(self):
        """
        Return the phase image if it exists, else ``None``.
        """
        return self.image('Phase')

    def image(self, image_type):
        """
        Returns the specified image type if it exists, else ``None``.
        """
        return self.images.get(image_type, None)

    def image_types(self):
        """
        Returns a list of names for all image types.
        """
        return list(self.images.keys())

    def describe_images(self):
        """
        Returns a list of tuples (key, info) describing the image types.
        """
        return [(k, self.image(k).description) for k in self.image_types()]

    def __iter__(self):
        for v in six.itervalues(self.images):
            yield v

    def _read_header(self, file_object, check_version=True):
        """
        Read the Nanoscope file header.

        :param file_object: Opened file
        :param check_version: Whether to enforce version checking for known
                              supported versions. Defaults to True.
        :raises UnsupportedVersion: If the version is not supported and version
                                    checking is enabled.
        """
        file_object.seek(0)
        for line in file_object:
            parameter = parse_parameter(line, self.encoding)
            if not self._validate_version(parameter) and check_version:
                raise UnsupportedVersion(parameter.hard_value)
            if self._handle_parameter(parameter, file_object):
                return

    def _read_image_data(self, file_object, image_type):
        """
        Read the raw data for the specified image type if it is in the file.

        :param image_type: String indicating which image type to read.
        :returns: A NanoscopeImage instance of the specified type
        :raises MissingImageData: If the image_type indicated is not in the file
        """
        if image_type not in self.config['_Images']:
            raise MissingImageData(image_type)

        config = self.config['_Images'][image_type]
        data_offset = config['Data offset']
        data_size = config['Bytes/pixel']
        number_lines = config['Number of lines']
        samples_per_line = config['Samps/line']
        data_length = config['Data length']

        file_object.seek(data_offset)
        # 2023年2月8日 修改，数据长度以config的data length为准，data_size可能不准确
        number_points = number_lines * samples_per_line
        data_size_2 = int(data_length/number_points)
        raw_data = (np.frombuffer(file_object.read(data_length),
                                  dtype='<i{}'.format(data_size_2),
                                  count=number_points)
                   .reshape((number_lines, samples_per_line)))
        # number_points = number_lines * samples_per_line
        # raw_data = (np.frombuffer(file_object.read(data_size * number_points),
        #                           dtype='<i{}'.format(data_size),
        #                           count=number_points)
        #            .reshape((number_lines, samples_per_line)))

        scan_size = self._get_config_fuzzy_key(config, ['Scan size', 'Scan Size'])

        self.images[image_type] = NanoscopeImage(
            image_type,
            raw_data,
            config['Bytes/pixel'],
            config['Z magnify'],
            self._get_sensitivity_value(image_type, 'Z scale'),
            self._get_sensitivity_value(image_type, 'Z offset'),
            scan_size * scan_size,
            config['Description'],
        )
        return self.images[image_type]

    def _get_sensitivity_value(self, image_type, key):
        parameter = self.config['_Images'][image_type][key]
        sensitivity = self.config[parameter.soft_scale]
        value = parameter.hard_value
        # print(type(sensitivity), sensitivity)
        # print(type(value), value)
        # 有些解析错误，如 Arb 这个单位
        return sensitivity * value

    def _get_config_fuzzy_key(self, config, keys):
        for k in keys:
            value = config.get(k, None)
            if value is not None:
                return value
        raise KeyError

    def _validate_version(self, parameter):
        if parameter.type == 'H' or parameter.parameter != 'Version':
            return True
        return parameter.hard_value in self.supported_versions

    def _handle_parameter(self, parameter, f):
        if parameter.type == 'H':  # header
            if parameter.header == 'File list end':
                return True
            if parameter.header == 'Ciao image list':
                return self._handle_parameter(self._read_image_header(f), f)
        elif parameter.type == 'V':
            if not parameter.soft_scale and not parameter.hard_scale:
                self.config[parameter.parameter] = parameter.hard_value
            else:
                self.config[parameter.parameter] = parameter
        elif parameter.type != 'S':
            self.config[parameter.parameter] = parameter.hard_value
        return False

    def _read_image_header(self, f):
        image_config = {}
        for line in f:
            parameter = parse_parameter(line, self.encoding)
            if parameter.type == 'H':
                return parameter
            elif parameter.type == 'S':
                if parameter.parameter == 'Image Data':
                    image_config['Image Data'] = parameter.internal
                    image_config['Description'] = parameter.external
                    self.config['_Images'][parameter.internal] = image_config
            elif parameter.type == 'V':
                if not parameter.soft_scale and not parameter.hard_scale:
                    image_config[parameter.parameter] = parameter.hard_value
                else:
                    image_config[parameter.parameter] = parameter
            else:
                image_config[parameter.parameter] = parameter.hard_value
