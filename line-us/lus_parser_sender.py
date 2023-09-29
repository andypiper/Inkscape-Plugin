# pylint: disable=line-too-long

# Version 2.0, 2023-09-26
# @andypiper
#
# Changes:
# - migrated to Inkscape 1.0 and 1.2+
# - tidied up UI
# - removed (some) cruft
# - pulled in changes from https://github.com/amyszczepanski/Inkscape-Plugin

# FIXME: consistency in var and func names (pylint) - NOTE: match in inx file
# TODO: docstrings
# TODO: debug handler
# TODO: implement tests -> so it can be added to Inkscape Gallery
# TODO: use zeroconf; also, fallback manual config for Line-us network address
# TODO: manual configuring filename for Gcode output
# TODO: add param gui-description values
# TODO: borrow a load of stuff from https://github.com/Line-us/LineUsPythonModule/tree/master
# TODO: detailed firmware info from Line-Us
# TODO: refactor to separate files

# lus_parser_sender.py
# Part of the Line-us extension for Inkscape
# By Yulya & Anatoly Besplemennov (@hihickster @longtolik)
# Version 1.4,  2018-03-24
# This program is based on SVG parser implemented in EggBot Inkscape Extension

# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA  02111-1307  USA

import gettext
import os
import sys
import socket
import time
import shlex

from argparse import ArgumentParser, Namespace
from math import sqrt
from lxml import etree

import inkex
from inkex import bezier
from inkex.elements import Group, PathElement
from inkex.paths import Path
from inkex.transforms import Transform
from inkex.utils import filename_arg

# delay (seconds) for the pen to go up/down before the next move
N_PEN_DELAY = 0.0
N_PAGE_HEIGHT = 2000  # Default page height (each unit equiv. to one step)
N_PAGE_WIDTH = 2000  # Default page width (each unit equiv. to one step)
N_PEN_UP_POS = 1000  # Default pen-up position
N_PEN_DOWN_POS = 0  # Default pen-down position
N_WALK_DEFAULT = 10  # Default steps for walking stepper motors
N_DEFAULT_LAYER = 1  # Default inkscape layer

PLATFORM = sys.platform.lower()

HOME = os.getenv('HOME')
USER = os.getenv('USER')

if PLATFORM == 'win32':
    HOME = os.path.realpath(
        "C:/")  # Arguably, this should be %APPDATA% or %TEMP%

Gcode_file = os.path.join(HOME, 'lus-output.txt')

# ----------------------------------------------------------------


def _lists(mat):
    return [list(row) for row in mat]


def compose_transform(mat1, mat2):
    """Transform(M1) * Transform(M2)"""
    return _lists((Transform(mat1) @ Transform(mat2)).matrix)


def parse_transform(transf, mat=None):
    """Transform(str).matrix"""
    t = Transform(transf)
    if mat is not None:
        t = Transform(mat) * t
    return _lists(t.matrix)


def parse_length_with_units(lwu):
    u = 'px'
    s = lwu.strip()
    if s[-2:] == 'px':
        s = s[:-2]
    elif s[-1:] == '%':
        u = '%'
        s = s[:-1]

    try:
        v = float(s)
    except ValueError as verr:
        return None, None

    return v, u


def subdivide_cubic_path(sp, flat, i=1):
    while True:
        while True:
            if i >= len(sp):
                return

            p0 = sp[i - 1][1]
            p1 = sp[i - 1][2]
            p2 = sp[i][0]
            p3 = sp[i][1]

            b = (p0, p1, p2, p3)

            if bezier.maxdist(b) > flat:
                break

            i += 1

        one, two = bezier.beziersplitatt(b, 0.5)
        sp[i - 1][2] = one[1]
        sp[i][0] = two[2]
        p = [one[2], one[3], two[1]]
        sp[i:1] = [p]


# -----------------------------------------------------------------------------------------------------
class LUS(inkex.EffectExtension):
    # -----------------------------------------------------------------------------------------------------
    def __init__(self):
        inkex.EffectExtension.__init__(self)

        self.arg_parser.add_argument("--smoothness",
                                     type=float,
                                     dest="smoothness", default=0.1,
                                     help="Smoothness of curves")

        self.arg_parser.add_argument("--penDelay",
                                     type=float,
                                     dest="penDelay", default=N_PEN_DELAY,
                                     help="Delay after pen lift/down (sec)")

        self.arg_parser.add_argument("--tab",
                                     type=str,
                                     dest="tab", default="controls",
                                     help="The active tab when Apply was pressed")

        self.arg_parser.add_argument("--penUpPosition",
                                     type=int,
                                     dest="penUpPosition", default=N_PEN_UP_POS,
                                     help="Position when lifted")

        self.arg_parser.add_argument("--penDownPosition",
                                     type=int,
                                     dest="penDownPosition", default=N_PEN_DOWN_POS,
                                     help="Position when lowered")
        self.arg_parser.add_argument("--layernumber",
                                     type=int,
                                     dest="layernumber", default=N_DEFAULT_LAYER,
                                     help="Selected layer for multilayer plotting")
        self.arg_parser.add_argument("--setupType",
                                     type=str,
                                     dest="setupType", default="controls",
                                     help="The active option when Apply was pressed")
        self.arg_parser.add_argument("--manualType",
                                     type=str,
                                     dest="manualType", default="controls",
                                     help="The active option when Apply was pressed")
        self.arg_parser.add_argument("--WalkDistance",
                                     type=int,
                                     dest="WalkDistance", default=N_WALK_DEFAULT,
                                     help="Selected layer for multilayer plotting")

        self.add_arguments(self.arg_parser)

        self.pen_is_up = True
        self.fX = None
        self.fY = None
        self.fPrevX = None
        self.fPrevY = None
        self.ptFirst = None
        self.node_count = int(0)
        self.node_target = int(0)
        self.path_count = int(0)
        self.layers_plotted = 0

        self.svg_layer = int(0)
        self.svg_node_count = int(0)
        self.svg_data_read = False
        self.svg_last_path = int(0)
        self.svg_last_path_NC = int(0)

        self.svg_total_delta_X = int(0)
        self.svg_total_delta_Y = int(0)

        n_delta_X = 0
        n_delta_Y = 0

        self.svg_width = float(N_PAGE_WIDTH)
        self.svg_height = float(N_PAGE_HEIGHT)
        self.svg_transform = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]
        self.warnings = {}
        self.step_scaling_factor = 1
        self.GF = False  # GF means to G-code File
        self.LU = False  # LU means send to Line-us

# ----------------------------------------------------------------------------

    def effect(self):
        # Main entry

        # self.svg = self.svg.select_all()
        self.svg = self.document.getroot()
        self.check_svg_for_lus_data()

# ____________	Output to Line-us  ______________________________

        if self.options.tab == 'splash':      # Plot
            self.LU = True
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.connect()
            self.all_layers = True
            self.plot_current_layer = True
            self.svg_node_count = 0
            self.svg_last_path = 0
            self.svg_layer = 12345  # indicate that we are plotting all layers.
            self.plot_to_lus()

        elif self.options.tab == 'manual':
            self.LU = True
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.connect()
            self.manual_command()

        elif self.options.tab == 'layers':
            self.LU = True
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.connect()
            self.all_layers = False
            self.plot_current_layer = False
            self.layers_plotted = 0
            self.svg_last_path = 0
            self.svg_node_count = 0
            self.svg_layer = self.options.layernumber
            self.plot_to_lus()
            if self.layers_plotted == 0:
                inkex.errormsg(gettext.gettext(
                    "Did not find any numbered layers to plot."))

# ____________	Output to G-code file  __________________

        elif self.options.tab == 'gcode':  # G-code
            # TODO: add comment to the output with current Inkscape file name and extension info
            self.GF = True
            with open(Gcode_file, 'w', encoding="utf8") as self.fil:
                # write header needed for Line-us
                self.fil.write('G54 X0 Y0 S1\n')
                self.all_layers = True
                self.plot_current_layer = True
                self.svg_node_count = 0
                self.svg_last_path = 0
                # indicate that we are plotting all layers.
                self.svg_layer = 12345
                self.plot_to_lus()

# ____________	Common  final section   ____________________________________

        self.svg_data_read = False
        self.update_svg_lus_data(self.svg)

        if self.LU:  # to Line-us
            self._sock.close()
        if self.GF:  # to Gcode file
            self.fil.close()

        self.LU = False
        self.GF = False

# -----------------------------------------------------------------------------------------------------

    def check_svg_for_lus_data(self):
        self.svg_data_read = False
        self.recursive_lus_data_scan(self.svg)
        if not self.svg_data_read:  # if there is no lus data, add some:
            # luslayer = self.svg.add(Group.new('lus', is_layer=True))
            luslayer = etree.SubElement(self.svg, 'lus')

            luslayer.set('layer', str(0))
            luslayer.set('node', str(0))
            luslayer.set('lastpath', str(0))
            luslayer.set('lastpathnc', str(0))
            luslayer.set('totaldeltax', str(0))
            luslayer.set('totaldeltay', str(0))

# -----------------------------------------------------------------------------------------------------

    def recursive_lus_data_scan(self, aNodeList):
        if not self.svg_data_read:
            for node in aNodeList:
                if node.tag == 'svg':
                    self.recursive_lus_data_scan(node)
                elif node.tag == inkex.addNS('botbot', 'svg') or node.tag == 'lus':

                    self.svg_layer = int(node.get('layer'))
                    self.svg_node_count = int(node.get('node'))

                    try:
                        self.svg_last_path = int(node.get('lastpath'))
                        self.svg_last_path_NC = int(node.get('lastpathnc'))
                        self.svg_total_delta_X = int(node.get('totaldeltax'))
                        self.svg_total_delta_Y = int(node.get('totaldeltay'))
                        self.svg_data_read = True
                    except ValueError as verr:
                        node.set('lastpath', str(0))
                        node.set('lastpathnc', str(0))
                        node.set('totaldeltax', str(0))
                        node.set('totaldeltay', str(0))
                        self.svg_data_read = True

# -----------------------------------------------------------------------------------------------------

    def update_svg_lus_data(self, aNodeList):
        if not self.svg_data_read:
            for node in aNodeList:
                if node.tag == 'svg':
                    self.update_svg_lus_data(node)
                elif node.tag == inkex.addNS('lus', 'svg') or node.tag == 'lus':
                    node.set('layer', str(self.svg_layer))
                    node.set('node', str(self.svg_node_count))
                    node.set('lastpath', str(self.svg_last_path))
                    node.set('lastpathnc', str(self.svg_last_path_NC))
                    node.set('totaldeltax', str(self.svg_total_delta_X))
                    node.set('totaldeltay', str(self.svg_total_delta_Y))
                    self.svg_data_read = True

# -----------------------------------------------------------------------------------------------------

    def manual_command(self):
        if self.options.manualType == "none":
            return

        if self.options.manualType == "raise_pen":
            self.pen_up()

        elif self.options.manualType == "lower_pen":
            self.pen_down()

        elif self.options.manualType == "version_check":
            version_info = self.get_hello_info()
            inkex.errormsg(version_info)

        # FIXME: this might be broken?
        elif self.options.manualType == "walk_X_motor" or "walk_Y_motor":
            if self.options.manualType == "walk_X_motor":
                nDeltaX = self.options.WalkDistance
                nDeltaY = 0
            elif self.options.manualType == "walk_Y_motor":
                nDeltaY = self.options.WalkDistance
                nDeltaX = 0
            else:
                return

            strOutput = ','.join(['G01 X'+str(nDeltaX)+' Y'+str(nDeltaY)])
            self.do_command(strOutput)

        return

# -----------------------------------------------------------------------------------------------------

    def plot_to_lus(self):
        # Plotting
        # parse the svg data as a series of line segments and send each segment to be plotted

        if not self.get_doc_properties():
            # Cannot handle the document's dimensions!!!
            inkex.errormsg(gettext.gettext(
                'The document to be plotted has invalid dimensions. ' +
                'The dimensions must be unitless, or have units of pixels (px) or ' +
                'percentages (%). Document dimensions may be set in Inkscape using ' +
                'File > Document Properties'))
            return

        # Viewbox handling
        # Also ignores the preserveAspectRatio attribute
        viewbox = self.svg.get('viewBox')
        if viewbox:
            vinfo = viewbox.strip().replace(',', ' ').split(' ')
            if (float(vinfo[2]) != 0) and (float(vinfo[3]) != 0):
                sx = self.svg_width / float(vinfo[2])
                sy = self.svg_height / float(vinfo[3])
                self.svg_transform = inkex.transforms.Transform(
                    'scale(%f,%f) translate(%f,%f)' % (sx, sy, -float(vinfo[0]), -float(vinfo[1])))

                # self.svgTransform = parseTransform('scale(%f,%f) translate(%f,%f)' % (
                #     sx, sy, -float(vinfo[0]), -float(vinfo[1])))
        try:
            self.recursively_traverse_svg(self.svg, self.svg_transform)

            if self.ptFirst:
                self.fX = self.ptFirst[0]
                self.fY = self.ptFirst[1]
                self.node_count = self.node_target    # enablesfpx return-to-home only option
                self.plot_line()

                # Return Home here
                self.pen_up()
                # or G28 Return to Home Position
                self.do_command('G01 X1000 Y1000')
                # self.doCommand( 'G01 Z1000' ) # or G28 Return to Home Position

                # _______ End of Plotting _______________________________________

            # inkex.errormsg('Final node count: ' + str(self.svgNodeCount))
            self.svg_layer = 0
            # self.svgNodeCount = 0
            self.svg_last_path = 0
            self.svg_last_path_NC = 0
            self.svg_total_delta_X = 0
            self.svg_total_delta_Y = 0
        finally:
            # We may have had an exception
            pass  # inkex.errormsg('End drawing')

# -----------------------------------------------------------------------------------------------------

    def recursively_traverse_svg(self, aNodeList,
                                 matCurrent=[[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]],
                                 parent_visibility='visible'):
        for node in aNodeList:
            # Ignore invisible nodes
            v = node.get('visibility', parent_visibility)
            if v == 'inherit':
                v = parent_visibility
            if v == 'hidden' or v == 'collapse':
                pass

            # first apply the current matrix transform to this node's tranform
            # matNew = matCurrent * \
            #     inkex.transforms.Transform(node.get("transform"))
            matNew = matCurrent @ inkex.transforms.Transform(
                node.get("transform"))

            # matNew = composeTransform(
            #     matCurrent, parseTransform(node.get("transform")))

            if node.tag == inkex.addNS('g', 'svg') or node.tag == 'g':
                # self.penUp()

                if node.get(inkex.addNS('groupmode', 'inkscape')) == 'layer':
                    if not self.all_layers:
                        # inkex.errormsg('Plotting layer named: ' + node.get(inkex.addNS('label', 'inkscape')))
                        self.do_we_plot_layer(
                            node.get(inkex.addNS('label', 'inkscape')))
                self.recursively_traverse_svg(
                    node, matNew, parent_visibility=v)

            elif node.tag == inkex.addNS('use', 'svg') or node.tag == 'use':

                # A <use> element refers to another SVG element via an xlink:href="#blah"
                # attribute.  We will handle the element by doing an XPath search through
                # the document, looking for the element with the matching id="blah"
                # attribute.  We then recursively process that element after applying
                # any necessary (x,y) translation.
                #
                # Notes:
                #  1. We ignore the height and width attributes as they do not apply to
                #     path-like elements, and
                #  2. Even if the use element has visibility="hidden", SVG still calls
                #     for processing the referenced element.  The referenced element is
                #     hidden only if its visibility is "inherit" or "hidden".

                refid = node.get(inkex.addNS('href', 'xlink'))
                if refid:
                    # [1:] to ignore leading '#' in reference
                    path = '//*[@id="%s"]' % refid[1:]
                    refnode = node.xpath(path)
                    if refnode:
                        x = float(node.get('x', '0'))
                        y = float(node.get('y', '0'))
                        # Note: the transform has already been applied
                        if (x != 0) or (y != 0):
                            matNew2 = compose_transform(
                                matNew, parse_transform('translate(%f,%f)' % (x, y)))
                        else:
                            matNew2 = matNew
                        v = node.get('visibility', v)
                        self.recursively_traverse_svg(
                            refnode, matNew2, parent_visibility=v)
                    else:
                        pass
                else:
                    pass

            elif node.tag == inkex.addNS('path', 'svg'):

                self.path_count += 1
                self.plot_path(node, matNew)
                self.svg_last_path += 1
                self.svg_last_path_NC = self.node_count

            elif node.tag == inkex.addNS('rect', 'svg') or node.tag == 'rect':

                # Manually transform
                #
                #    <rect x="X" y="Y" width="W" height="H"/>
                #
                # into
                #
                #    <path d="MX,Y lW,0 l0,H l-W,0 z"/>
                #
                # I.e., explicitly draw three sides of the rectangle and the
                # fourth side implicitly

                # Create a path with the outline of the rectangle
                # newpath = inkex.etree.Element(inkex.addNS('path', 'svg'))
                newpath = PathElement()
                x = float(node.get('x'))
                y = float(node.get('y'))
                w = float(node.get('width'))
                h = float(node.get('height'))
                # s = node.get('style')
                # if s:
                #     newpath.set('style', s)
                t = node.get('transform')
                if t:
                    # newpath.set('transform', t)
                    newpath.apply_transform(t)
                a = ''
                a += 'M ' + str(x) + ' ' + str(y)
                a += ' l ' + str(w) + ' 0'
                a += ' l 0 ' + str(h)
                a += ' l ' + str(-w) + ' 0'
                a += ' Z'
                newpath.path = a
                # a = []
                # a.append(['M ', [x, y]])
                # a.append([' l ', [w, 0]])
                # a.append([' l ', [0, h]])
                # a.append([' l ', [-w, 0]])
                # a.append([' Z', []])
                # newpath.set('d', simplepath.formatPath(a))
                self.plot_path(newpath, matNew)

            elif node.tag == inkex.addNS('line', 'svg') or node.tag == 'line':

                # Convert
                #
                #   <line x1="X1" y1="Y1" x2="X2" y2="Y2/>
                #
                # to
                #
                #   <path d="MX1,Y1 LX2,Y2"/>

                self.path_count += 1

                # Create a path to contain the line
                newpath = PathElement()
                # newpath = inkex.etree.Element(inkex.addNS('path', 'svg'))
                x1 = float(node.get('x1'))
                y1 = float(node.get('y1'))
                x2 = float(node.get('x2'))
                y2 = float(node.get('y2'))
                # s = node.get('style')
                # if s:
                #     newpath.set('style', s)
                t = node.get('transform')
                if t:
                    newpath.set('transform', t)
                # a = []
                # a.append(['M ', [x1, y1]])
                # a.append([' L ', [x2, y2]])
                # newpath.set('d', simplepath.formatPath(a))
                a = ''
                a += 'M ' + str(x1) + ' ' + str(y1)
                a += ' L ' + str(x2) + ' ' + str(y2)
                newpath.path = a
                self.plot_path(newpath, matNew)
                self.svg_last_path += 1
                self.svg_last_path_NC = self.node_count

            elif node.tag == inkex.addNS('polyline', 'svg') or node.tag == 'polyline':

                # Convert
                #
                #  <polyline points="x1,y1 x2,y2 x3,y3 [...]"/>
                #
                # to
                #
                #   <path d="Mx1,y1 Lx2,y2 Lx3,y3 [...]"/>
                #
                # Note: we ignore polylines with no points

                pl = node.get('points', '').strip()
                if pl == '':
                    pass

                self.path_count += 1

                pa = pl.split()
                if not len(pa):
                    pass
                # Issue 29: pre 2.5.? versions of Python do not have
                #    "statement-1 if expression-1 else statement-2"
                # which came out of PEP 308, Conditional Expressions
                # d = "".join( ["M " + pa[i] if i == 0 else " L " + pa[i] for i in range( 0, len( pa ) )] )
                d = "M " + pa[0]
                for i in range(1, len(pa)):
                    d += " L " + pa[i]
                # newpath = inkex.etree.Element(inkex.addNS('path', 'svg'))
                # newpath.set('d', d)
                # s = node.get('style')
                # if s:
                #     newpath.set('style', s)
                newpath = PathElement()
                newpath.path = d
                t = node.get('transform')
                if t:
                    newpath.set('transform', t)
                self.plot_path(newpath, matNew)
                self.svg_last_path += 1
                self.svg_last_path_NC = self.node_count

            elif node.tag == inkex.addNS('polygon', 'svg') or node.tag == 'polygon':

                # Convert
                #
                #  <polygon points="x1,y1 x2,y2 x3,y3 [...]"/>
                #
                # to
                #
                #   <path d="Mx1,y1 Lx2,y2 Lx3,y3 [...] Z"/>
                #
                # Note: we ignore polygons with no points

                pl = node.get('points', '').strip()
                if pl == '':
                    pass

                self.path_count += 1

                pa = pl.split()
                if not len(pa):
                    pass
                # Issue 29: pre 2.5.? versions of Python do not have
                #    "statement-1 if expression-1 else statement-2"
                # which came out of PEP 308, Conditional Expressions
                # d = "".join( ["M " + pa[i] if i == 0 else " L " + pa[i] for i in range( 0, len( pa ) )] )
                d = "M " + pa[0]
                for i in range(1, len(pa)):
                    d += " L " + pa[i]
                d += " Z"
                # newpath = inkex.etree.Element(inkex.addNS('path', 'svg'))
                # newpath.set('d', d)
                # s = node.get('style')
                # if s:
                #     newpath.set('style', s)
                newpath = PathElement()
                newpath.path = d
                t = node.get('transform')
                if t:
                    newpath.set('transform', t)
                self.plot_path(newpath, matNew)
                self.svg_last_path += 1
                self.svg_last_path_NC = self.node_count

            elif node.tag == inkex.addNS('ellipse', 'svg') or \
                    node.tag == 'ellipse' or \
                    node.tag == inkex.addNS('circle', 'svg') or \
                    node.tag == 'circle':

                # Convert circles and ellipses to a path with two 180 degree arcs.
                # In general (an ellipse), we convert
                #
                #   <ellipse rx="RX" ry="RY" cx="X" cy="Y"/>
                #
                # to
                #
                #   <path d="MX1,CY A RX,RY 0 1 0 X2,CY A RX,RY 0 1 0 X1,CY"/>
                #
                # where
                #
                #   X1 = CX - RX
                #   X2 = CX + RX
                #
                # Note: ellipses or circles with a radius attribute of value 0 are ignored

                if node.tag == inkex.addNS('ellipse', 'svg') or node.tag == 'ellipse':
                    rx = float(node.get('rx', '0'))
                    ry = float(node.get('ry', '0'))
                else:
                    rx = float(node.get('r', '0'))
                    ry = rx
                if rx == 0 or ry == 0:
                    pass

                self.path_count += 1

                cx = float(node.get('cx', '0'))
                cy = float(node.get('cy', '0'))
                x1 = cx - rx
                x2 = cx + rx
                d = 'M %f,%f ' % (x1, cy) + \
                    'A %f,%f ' % (rx, ry) + \
                    '0 1 0 %f,%f ' % (x2, cy) + \
                    'A %f,%f ' % (rx, ry) + \
                    '0 1 0 %f,%f' % (x1, cy)
                # newpath = inkex.etree.Element(inkex.addNS('path', 'svg'))
                # newpath.set('d', d)
                # s = node.get('style')
                # if s:
                #     newpath.set('style', s)
                newpath = PathElement()
                newpath.path = d
                t = node.get('transform')
                if t:
                    newpath.set('transform', t)
                self.plot_path(newpath, matNew)
                self.svg_last_path += 1
                self.svg_last_path_NC = self.node_count
            elif node.tag == inkex.addNS('metadata', 'svg') or node.tag == 'metadata':
                pass
            elif node.tag == inkex.addNS('defs', 'svg') or node.tag == 'defs':
                pass
            elif node.tag == inkex.addNS('namedview', 'sodipodi') or node.tag == 'namedview':
                pass
            elif node.tag == inkex.addNS('lus', 'svg') or node.tag == 'lus':
                pass
            elif node.tag == inkex.addNS('title', 'svg') or node.tag == 'title':
                pass
            elif node.tag == inkex.addNS('desc', 'svg') or node.tag == 'desc':
                pass
            elif node.tag == inkex.addNS('text', 'svg') or node.tag == 'text':
                if not self.warnings.has_key('text'):
                    inkex.errormsg(gettext.gettext('Warning: unable to draw text; ' +
                                                   'please convert it to a path first.  Consider using the ' +
                                                   'Hershey Text extension which is located under the ' +
                                                   '"Render" category of extensions.'))
                    self.warnings['text'] = 1
                pass
            elif node.tag == inkex.addNS('image', 'svg') or node.tag == 'image':
                if not self.warnings.has_key('image'):
                    inkex.errormsg(gettext.gettext('Warning: unable to draw bitmap images; ' +
                                                   'please convert them to line art first.  Consider using the "Trace bitmap..." ' +
                                                   'tool of the "Path" menu.  Mac users please note that some X11 settings may ' +
                                                   'cause cut-and-paste operations to paste in bitmap copies.'))
                    self.warnings['image'] = 1
                pass
            elif node.tag == inkex.addNS('pattern', 'svg') or node.tag == 'pattern':
                pass
            elif node.tag == inkex.addNS('radialGradient', 'svg') or node.tag == 'radialGradient':
                # Similar to pattern
                pass
            elif node.tag == inkex.addNS('linearGradient', 'svg') or node.tag == 'linearGradient':
                # Similar in pattern
                pass
            elif node.tag == inkex.addNS('style', 'svg') or node.tag == 'style':
                # This is a reference to an external style sheet and not the value
                # of a style attribute to be inherited by child elements
                pass
            elif node.tag == inkex.addNS('cursor', 'svg') or node.tag == 'cursor':
                pass
            elif node.tag == inkex.addNS('color-profile', 'svg') or node.tag == 'color-profile':
                # Gamma curves, color temp, etc. are not relevant to single color output
                pass
            elif not isinstance(node.tag, str):
                # This is likely an XML processing instruction such as an XML
                # comment.  lxml uses a function reference for such node tags
                # and as such the node tag is likely not a printable string.
                # Further, converting it to a printable string likely won't
                # be very useful.
                pass
            else:
                if not self.warnings.has_key(str(node.tag)):
                    t = str(node.tag).split('}')
                    inkex.errormsg(gettext.gettext('Warning: unable to draw <' + str(t[-1]) +
                                                   '> object, please convert it to a path first.'))
                    self.warnings[str(node.tag)] = 1
                pass

# -----------------------------------------------------------------------------------------------------

    def do_we_plot_layer(self, strLayerName):

        temp_num_string = 'x'
        string_pos = 1
        current_layer_name = strLayerName.lstrip()  # remove leading whitespace

        # Look at layer name.  Sample first character, then first two, and
        # so on, until the string ends or the string no longer consists of
        # digit characters only.

        MaxLength = len(current_layer_name)
        if MaxLength > 0:
            while string_pos <= MaxLength:
                if str.isdigit(current_layer_name[:string_pos]):
                    # Store longest numeric string so far
                    temp_num_string = current_layer_name[:string_pos]
                    string_pos = string_pos + 1
                else:
                    break

        # Temporarily assume that we aren't plotting the layer
        self.plot_current_layer = False
        if str.isdigit(temp_num_string):
            if self.svg_layer == int(float(temp_num_string)):
                self.plot_current_layer = True  # We get to plot the layer!
                self.layers_plotted += 1
        # Note: this function is only called if we are NOT plotting all layers.

# -----------------------------------------------------------------------------------------------------

    def get_length(self, name, default):

        str = self.svg.get(name)
        if str:
            v, u = parse_length_with_units(str)
            if not v:
                # Couldn't parse the value
                return None
            elif (u == '') or (u == 'px'):
                return v
            elif u == '%':
                return float(default) * v / 100.0
            else:
                # Unsupported units
                return None
        else:
            # No width specified; assume the default value
            return float(default)

# -----------------------------------------------------------------------------------------------------

    def distance(self, x, y):
        return sqrt(x * x + y * y)

# -----------------------------------------------------------------------------------------------------

    def get_doc_properties(self):

        self.svg_height = self.get_length('height', N_PAGE_HEIGHT)
        self.svg_width = self.get_length('width', N_PAGE_WIDTH)
        if (self.svg_height is None) or (self.svg_width is None):
            return False

        return True

# -----------------------------------------------------------------------------------------------------

    def plot_path(self, path, matTransform):

        # turn this path into a cubicsuperpath (list of beziers)...

        d = path.get('d')

        # if len(simplepath.parsePath(d)) == 0:
        if len(Path(d).to_arrays()) == 0:
            return

        # p = cubicsuperpath.parsePath(d)
        p = inkex.paths.CubicSuperPath(inkex.paths.Path(d))

        # ...and apply the transformation to each point
        # applyTransformToPath(matTransform, p)
        Path(p).transform(Transform(matTransform)).to_arrays()

        # p is now a list of lists of cubic beziers [control pt1, control pt2, endpoint]
        # where the start-point is the last point in the previous segment.
        for sp in p:
            subdivide_cubic_path(sp, self.options.smoothness)

            nIndex = 0
            for csp in sp:
                self.fX = float(csp[1][0])
                self.fY = float(csp[1][1])
                # home
                if self.ptFirst is None:
                    # self.svgWidth/2  #( 2 * self.step_scaling_factor )
                    self.fPrevX = 0
                    # ( 2 * self.step_scaling_factor )
                    self.fPrevY = self.svg_height
                    self.ptFirst = (self.fPrevX, self.fPrevY)

                if self.plot_current_layer:
                    self.plot_line()
                    self.fPrevX = self.fX
                    self.fPrevY = self.fY
                # self.doCommand(str(nIndex ))
                if self.plot_current_layer:
                    if nIndex == 0:
                        self.pen_up()
                    elif nIndex == 1:
                        self.pen_down()
                nIndex += 1

# -----------------------------------------------------------------------------------------------------

    def pen_up(self):
        if not self.pen_is_up:
            self.pen_is_up = True
            if self.LU:
                # self.doCommand( 'G01 Z'+str(self.options.penUpPosition)) # for future needs
                self.do_command('G01 Z1000')  # for a while
                time.sleep(self.options.penDelay)

# -----------------------------------------------------------------------------------------------------

    def pen_down(self):
        if self.pen_is_up:
            self.pen_is_up = False
            if self.LU:
                # self.doCommand( 'G01 Z'+str(self.options.penDownPosition)) # for future needs
                self.do_command('G01 Z0')   # for a while
                time.sleep(self.options.penDelay)

# -----------------------------------------------------------------------------------------------------

    def plot_line(self):
        if self.fPrevX is None:
            return

        n_delta_x = self.fX - self.fPrevX
        n_delta_y = self.fY - self.fPrevY

        if self.distance(n_delta_x, n_delta_y) > 0:
            self.node_count += 1

            while ((abs(n_delta_x) > 0) or (abs(n_delta_y) > 0)):
                xd = n_delta_x
                yd = n_delta_y

                xt = self.svg_total_delta_X
                yt = -self.svg_total_delta_Y

                if self.LU:  # to Lineus
                    # strOutput = ','.join( ['G01 X'+("%d" % xt)+' Y'+("%d" % yt)])

                    if xt*yt != 0:   # such a patch
                        strOutput = ','.join(
                            ['G01 X'+("%d" % xt)+' Y'+("%d" % yt)])
                    else:
                        # just lift the pen
                        strOutput = ','.join(['G01 Z1000'])

                if self.GF:  # to Gcode file
                    if not self.pen_is_up:
                        strOutput = ','.join(
                            ['G01 X'+("%d" % xt)+' Y'+("%d" % yt)+' Z0'])
                    else:
                        strOutput = ','.join(
                            ['G01 X'+("%d" % xt)+' Y'+("%d" % yt)+' Z1000'])
                        self.do_command(strOutput)
                        strOutput = ','.join(
                            ['G01 X'+("%d" % xt)+' Y'+("%d" % yt)+' Z0'])
                self.do_command(strOutput)

                self.svg_total_delta_X += xd
                self.svg_total_delta_Y += yd

                n_delta_x -= xd
                n_delta_y -= yd

# -----------------------------------------------------------------------------------------------------

    def do_command(self, cmd):

        if self.LU:  # to Line-us
            # cmd += b'\x00'
            cmd += ''
            response = ''
            try:
                self.send_cmd(cmd)
                while response == '':
                    response = self.get_resp()
                    inkex.errormsg(str(response))
                if response[0] != 'o':
                    inkex.errormsg(cmd)
                    inkex.errormsg(str(response))
                    time.sleep(0.5)
                    self.send_cmd(cmd)  # put it again
                    inkex.errormsg('Repeated: ' + cmd)
            except Exception as err:
                pass

        if self.GF:  # to Gcode File
            cmd += '\n'
            try:
                self.send_cmd(cmd)
            except Exception as err:
                pass

# -----------------------------------------------------------------------------------------------------

    # def doRequest(self):
    #     line = 'Not connected'
    #     if self.connected:
    #         self._sock.send(b'Hello')
    #         line = self.get_resp()
    #     return line

    def get_hello_info(self):
        line = 'Not connected'
        hello = {}
        if self.connected:
            self._sock.send(b'')
            line = shlex.split(self.get_resp())
            if line.pop(0) != 'hello':
                return None
            for field in line:
                split_fields = field.split(':', 1)
                hello[split_fields[0]] = split_fields[1]
            hello_msg = f"Version: {hello['VERSION']}\nSerial: {hello['SERIAL']}."
            return hello_msg
        else:
            return line

# -----------------------------------------------------------------------------------------------------

    def connect(self):
        try:
            self._sock.connect(('line-us.local', 1337))  # Common
            inkex.errormsg('Connected')
            self.connected = True
        except ConnectionError as connerr:
            inkex.errormsg(gettext.gettext('Not connected'))
            self.connected = False

# -----------------------------------------------------------------------------------------------------

    def get_resp(self):
        if not self.connected:
            return None
        tim = 0
        lin = b''
        while tim < 1000:  # try for 10 seconds
            char = self._sock.recv(1)
            if char != b'\x00':
                lin += char
                tim = 0
            elif char == b'\x00':
                break
            tim = tim+1
            time.sleep(0.01)

        if tim > 990:
            lin = b'Time_out'

        return lin.decode('utf-8')

# -----------------------------------------------------------------------------------------------------

    def send_cmd(self, cmd):
        if self.LU:  # to Line-us
            if self.connected:
                # self._sock.send(cmd)
                cmd += '\r\n\0'
                self._sock.sendall(cmd.encode('utf-8'))
        if self.GF:  # to Gcode file
            self.fil.write(cmd)


# -----------------------------------------------------------------------------------------------------

if __name__ == '__main__':
    LUS().run()
