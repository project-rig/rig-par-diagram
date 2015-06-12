"""Cairo-based diagram generation of placement/routing information."""

import random

import cairocffi as cairo

from collections import defaultdict

from math import sin, cos, atan2, pi, sqrt

from six import iteritems, itervalues, next

from rig.geometry import shortest_torus_path, shortest_mesh_path

from rig.machine import Links, Cores

from rig.netlist import Net
from rig.place_and_route.constraints import ReserveResourceConstraint

from rig.place_and_route.routing_tree import RoutingTree

import rig

from parspective.geometry import get_core_ring_position

from parspective.style import PolygonStyle


default_chip_style = PolygonStyle(fill=(1.0, 1.0, 1.0, 0.0),
                                  stroke=(0.0, 0.0, 0.0, 1.0),
                                  line_width=0.03)

default_link_style = PolygonStyle(fill=(0.5, 0.5, 0.5, 0.2),
                                  stroke=(0.5, 0.5, 0.5, 0.25),
                                  line_width=0.025)

default_core_style = PolygonStyle(fill=(0.0, 0.0, 1.0, 1.0),
                                  stroke=(0.0, 0.0, 0.0, 0.0),
                                  line_width=0.005)
# Style for non-allocated cores
default_core_style.set(None, "fill", (1.0, 1.0, 1.0, 0.5))
default_core_style.set(None, "stroke", (0.0, 0.0, 1.0, 1.0))

default_net_style = PolygonStyle(stroke=(1.0, 0.0, 0.0, 0.5))

class Diagram(object):
    """A SpiNNaker machine placement diagram."""
    
    def __init__(
            self,
            # Problem graph specification
            machine, vertices_resources={}, nets=[], constraints=[],
            # Place & Route solution
            placements={}, allocations={}, routes={}, core_resource=Cores,
            # Chip style parameters
            chip_style=default_chip_style,
            chip_spacing=0.1,
            # Link style parameters
            link_style=default_link_style,
            # Core style parameters
            core_style=default_core_style,
            core_spacing=0.04,
            # Net style parameters
            net_style=default_net_style,
            net_min_line_width=0.005,
            net_weight_scale=None,
            # Ratsnest style parameters
            ratsnest_alpha=0.5,
            ratsnest_arc_height=0.1,
            ratsnest_loop_height=0.5,
            ratsnest_loop_angle=pi / 5.0):
        """Draw a diagram of a machine, placement or routing solution.
        
        The diagram is rendered onto the supplied Cairo context at the specified
        width and height.
        
        All sizes in the parameters below are in units of chip-widths (i.e. a chip
        is 1 unit wide).
        
        Parameters
        ----------
        ctx : :py:class:`cairo.Context`
            The Cairo context to render the diagram onto.
        width : float
        height : float
            Maximum width and height of the rendered diagram. The diagram will be
            rendered centered between (0, 0) and (width, height).
        machine : :py:class:`rig.machine.Machine`
            A description of the machine to draw.
        vertices_resources : {vertex: {resource: value}}
            The resources allocated to each vertex. See Rig's documentation for
            details.
        nets : [:py:class:`rig.netlist.Net`, ...]
            The list of nets which connect the vertices listed in vertices_resources.
            See Rig's documentation for details.
        constraints : [constraint, ...]
            The list of constraints imposed on the placement/routing solution.  See
            Rig's documentation for details.
        placements : {vertex: (x, y), ...}
            The coordinates of the chip each vertex is placed on. See Rig's
            documentation for details.
        allocations : {vertex: {resource: slice, ...}, ...}
            The resources allocated to each vertex. See Rig's documentation for
            details.
        routes : {:py:class:`rig.netlist.Net`: \
                  :py:class:`rig.routing_tree.RoutingTree`, ...}
            The routes assocated with each net. See Rig's documentation for details.
            
            If left empty, the nets will be drawn as a "ratsnest" with a line being
            drawn between the product of source and destination cores.
        core_resource : resource
            The type resource type which represents cores in a SpiNNaker chip.
            Defaults to :py:class:`rig.machine.Cores`. See Rig's documentation for
            details.
            
            Any vertices which don't have this resource allocated to them will not
            be drawn.
        chip_style : :py:class:`parspective.style.PolygonStyle`
            The style with which chips are drawn. Each chip is represented as a
            hexagon which will be styled with the supplied style. Style exceptions
            for (x, y) coordinates can be used to individually control the
            appearance of specific chips.
        chip_spacing : float
            The amount of space between neighbouring chips. Note that this space is
            where the chip-to-chip links are drawn and so should be set > 0.
        link_style : :py:class:`parspective.style.PolygonStyle`
            The style with which chip-to-chip links are drawn. Links are drawn as a
            solid rectangle connecting the corresponding edges of a hexagonal chip.
            Dead links are not drawn. Style exceptions may be listed for (x, y,
            link) tuples to style individual links differently. Note that
            both ends of a link must be given the same style exception otherwise
            the behaviour is undefined.
        core_style : :py:class:`parspective.style.PolygonStyle`
            The style with which cores are drawn. Each core is represented as a
            circle inside its associated chip, styled with the supplied style. The
            following style exceptions are supported to control the styling of
            individual cores:
            
            * None: Used by cores which don't have a vertex or constraint on them.
            * vertex: Styles all cores allocated to a particular vertex.
            * constraint: Styles all cores consumed by a
              :py:class:`rig.place_and_route.constraints.ReserveResourceConstraint`.
        core_spacing : float
            The amount of space between the cores drawn inside each chip.
        net_style : :py:class:`parspective.style.PolygonStyle`
            The style which is used to draw the paths of routed nets and ratsnests.
            Note that if line_width is not specified, the line-width will be set
            based on the net's weight (recommended). Style exceptions can be given
            for each net which allow individual nets to be styled differently.
        net_min_line_width : float
            The minimum line width to use to draw a net.
        net_weight_scale : float or None
            The multiplicative scaling factor which maps from net weight to net line
            thickness.
            
            If None (the default), this value is set such that no chip-to-chip link
            is visually overflowed and such that nets are not drawn ridiculously
            thick.
            
            This should generally only be set manually when making diagrams with
            consistent scales is important. If set too high then some nets may spill
            out over the chip-to-chip link boundaries.
        net_loop_height : float
        net_loop_angle : float
            Self-connections to cores are drawn as small loops. These two parameters
            control the height and opening-angle of that loop.
        ratsnest_alpha : float
            The ratsnest will be rendered with this opacity. Since the ratsnest can
            be visually very messy it may be useful to reduce its opacity to reduce
            the level of distraction.
        ratsnest_arc_height : float
            The core-to-core ratsnest connections are drawn with a slight arc so
            that connections travelling bidirectionally can be distinguished. This
            parameter indicates the approximate height of the arc. Setting this
            value to 0 makes all ratsnest lines straight.
        """
        # Store all the parameters...
        self.machine = machine
        self.vertices_resources = vertices_resources
        self.nets = nets
        self.constraints = constraints
        
        self.placements = placements
        self.allocations = allocations
        self.routes = routes
        self.core_resource = core_resource
        
        self.chip_style = chip_style
        self.chip_spacing = chip_spacing
        
        self.link_style = link_style
        
        self.core_style = core_style
        self.core_spacing = core_spacing
        
        self.net_style = net_style
        self.net_min_line_width = net_min_line_width
        self.net_weight_scale = net_weight_scale
        
        self.ratsnest_alpha = ratsnest_alpha
        self.ratsnest_arc_height = ratsnest_arc_height
        self.ratsnest_loop_height = ratsnest_loop_height
        self.ratsnest_loop_angle = ratsnest_loop_angle
        
        self.has_wrap_around_links = self.machine.has_wrap_around_links()
        
        # Work out what all cores are doing (e.g. what is placed on each core)
        # and what links pass through each net.
        self._init_core_map()
        self._allocate_nets_to_links()
        
        # Based on the cores and nets allocated, work out how to scale them so
        # that they fit
        self._calculate_core_sizes()
        self._calculate_net_sizes()
        
        # Calculate the canvas positions of nets in each link
        self._calculate_link_net_positions()
        
        
    def _init_core_map(self):
        """Initialise self._core_map with the contents of all cores."""
        # This dictionary specifies the contents of every core in the system.
        # {(x, y): {core: object, ...}, ...}.  The object may be one of: the
        # vertex allocated to that core, the ReserveResourceConstraints which
        # reserved that core or None if the core is unused (but working).
        self._core_map = defaultdict(dict)
        
        # Initially add None entries for all working cores in the machine.
        for xy in self.machine:
            for core_num in range(self.machine[xy].get(self.core_resource, 0)):
                self._core_map[xy][core_num] = None
        
        # Add cores reserved by ReserveResourceConstraints.
        for constraint in self.constraints:
            if isinstance(constraint, ReserveResourceConstraint):
                if constraint.resource == self.core_resource:
                    if constraint.location is None:
                        locations = iter(machine)
                    else:
                        locations = [constraint.location]
                    for xy in locations:
                        for core_num in range(constraint.reservation.start,
                                              constraint.reservation.stop):
                            self._core_map[xy][core_num] = constraint
        
        # Add placed vertices
        for vertex, xy in iteritems(self.placements):
            core_slice = self.allocations[vertex].get(self.core_resource,
                                                      slice(0, 0))
            for core_num in range(core_slice.start, core_slice.stop):
                self._core_map[xy][core_num] = vertex
        
    def _allocate_nets_to_links(self):
        """Initialise self._link_nets with an allocated ordering of
        (routed) nets within chip-to-chip links."""
        # This lookup gives the ordering of nets within links. Note that
        # the lookup is populated for both link directions.
        # {(x, y, link): [Net, ...]
        self._link_nets = defaultdict(list)
        
        for net, tree in iteritems(routes):
            for node in tree:
                # The tree iterator also iterates over vertices at the leaves of
                # the tree. Since these do not correspond with segments of the
                # net which traverse a chip-to-chip link, we can skip these.
                if isinstance(node, RoutingTree):
                    for direction, child in node.children:
                        if direction is not None and direction.is_link:
                            link = Links(direction)
                            self._add_net_to_link(net,
                                                  node.chip[0],
                                                  node.chip[1],
                                                  link)
    
    
    def _add_net_to_link(self, net, x1, y1, link1):
        """Add a net to a specific link in the self._link_nets structure.
        
        Parameters
        ----------
        net : :py:class:`rig.netlist.Net`
            The net which is passing through the link.
        x1 : int
        y1 : int
            The chip x and y coordinates from which the link the net traverses
            begins.
        link1 : :py:class:`rig.machine.Links`
            The link down which the net traverses.
        """
        # Find the chip at the opposite end of the link
        x2, y2, link2 = self._opposite_link(x1, y1, link1)
        
        # Add to the link net allocation in each direction
        self._link_nets[(x1, y1, link1)].append(net)
        self._link_nets[(x2, y2, link2)].insert(0, net)
    
    
    def _calculate_core_sizes(self):
        """Calculate how large the cores should be."""
        # To calculate the diameter of a core we must first know how many cores
        # will be packed into a chip. To make the scaling consistent across the
        # diagram, the tightest case (maximum number of cores) is used to decide
        # the size of a core.
        max_cores_per_chip = max(map(len, itervalues(self._core_map)))
        
        # The most efficient way of packing circles is the same as concentric
        # rings of hexagons so we must find out how many layers of concentric
        # rings are required to fit the set of cores.
        layers = get_core_ring_position(max_cores_per_chip,
                                        max_cores_per_chip - 1)[0] + 1
        
        # Given the number of layers and the spacing between cores it is now
        # possible to calculate the diameter of the core which leaves the
        # required size of gaps.
        self._core_diameter = (
                               # The amount of space along the diameter of the
                               # chip for cores:
                                  (
                                   # The inner diameter of the chip
                                   ((1.0 - self.chip_style.get("line_width")) *
                                    cos(pi / 6.0)) -
                                   # Take away the space required by gaps
                                   # between cores
                                   (self.core_spacing * layers * 2.0)
                                  ) /
                               # Divide by the number of cores which must fit in
                               # that space to get the diameter of a core.
                                  ((layers * 2.0) - 1.0)
                              )
        
    
    def _calculate_net_sizes(self):
        """Work out the scaling factors for net weights so that all nets fit
        neatly.
        """
        # Width of the rectangle between a pair of chips which represents a link
        # is simply the size of a side of the hexagon (less the stroke width).
        self._link_width = (sin(pi / 6.0) -
                            (self.link_style.get("line_width") / 2.0))
        
        # Compute a scaling factor for converting from net weights to stroke
        # with if not supplied.
        if self.net_weight_scale is None:
            # The default weight scaling factor is computed such that:
            # * No chip-to-chip link is overflowed by the nets crossing it.
            # * The most heavily weighted nets should not be insanely thick.
           
            # The maximum line width of a single net (a fraction of the size of
            # a core).
            max_allowed_net_width = self._core_diameter * 0.333
            
            # Determine the maximum weight allocated to any net
            max_net_weight = max(n.weight for n in self.nets)
            
            self.net_weight_scale = max_allowed_net_width / max_net_weight
            
            if self.routes:
                # The maximum width a link full of nets can become without
                # overflowing.
                max_allowed_link_width = (self._link_width -
                                          self.link_style.get("line_width")) * 0.9
                
                # Find the link with the largest combined weight of nets (including
                # spaces between them the same width as the net).
                max_link_weight = 0.0
                for (x, y, direction), nets in iteritems(self._link_nets):
                    link_weight = sum(n.weight for n in nets) * 2.0
                    max_link_weight = max(max_link_weight, link_weight)
                
                # Select the scaling factor which satisfies the above conditions.
                self.net_weight_scale = min(
                    self.net_weight_scale,
                    max_allowed_link_width / max_link_weight
                )
    
    
    def _opposite_link(self, x, y, link):
        """Given a link, find the opposite end.
        
        Parameters
        ----------
        x : int
        y : int
            The chip coordinates of the outgoing link.
        link : :py:class:`rig.machine.Links`
            The link leaving the specified chip.
        
        Returns
        -------
        (x, y, link)
            Gives the chip which the specified link arrives at and also
            identifies the link on which it arrives.
        """
        dx, dy = link.to_vector()
        x = (x + dx) % self.machine.width
        y = (y + dy) % self.machine.height
        link = link.opposite
        
        return (x, y, link)
    
    
    def _core_offset(self, num_cores, core_num):
        """Get the offset of a particular core from the centre of a chip.
        
        Parameters
        ----------
        num_cores : int
            The total number of cores on the chip.
        core_num : int
            The index of the core whose position is requested.
        """
        # In order to simplify the implementation of this method, the positions
        # of all cores on a num_cores chip is computed all at once. The
        # positions are then cached for later callers.
        
        # Create an empty cache
        # {num_cores: {core_num: (dx, dy), ...}, ...}
        if not hasattr(self, "_core_offsets"):
            self._core_offsets = {}
        
        if num_cores in self._core_offsets:
            # Return the cached value, if present
            return self._core_offsets[num_cores][core_num]
        else:
            # Work out the position offset of every core on a chip with
            # num_cores.
            
            offsets = {}
            for num in range(num_cores):
                # Cores will be arranged in concentric rings of hexagons like so:
                #      2 2 2
                #     2 1 1 2         1
                #    2 1 0 1 2   or    0 1   (if the outer layer is not full)
                #     2 1 1 2         1
                #      2 2 2
                # First determine the layer and index within that layer of the
                # current core.
                layer, index, num_in_layer = get_core_ring_position(num_cores,
                                                                    num)
                
                # Map that to a point around a circle of the radius of the
                # layer. Note that in layers which aren't full the hexagons are
                # spread evenly over the available space.
                # XXX: Should probably be a point around a hexagon...
                radius = layer * (self._core_diameter + self.core_spacing)
                angle = 2.0 * pi * (float(index) / num_in_layer)
                x = radius * sin(angle)
                y = radius * cos(angle)
                
                offsets[num] = (x, y)
            
            self._core_offsets[num_cores] = offsets
            
            # Return the newly computed answer
            return self._core_offsets[num_cores][core_num]
    
    
    def _chip(self, x, y):
        """Get the canvas coordinates of the centre of a chip."""
        # Cairo coordinates are top-to-bottom, chip coordinates are given
        # bottom-to-top.
        y = self.machine.height - y - 1
        
        # Add spacing between chips
        x *= (1.0 + self.chip_spacing)
        y *= (1.0 + self.chip_spacing)
        
        # Place on skewed coordinates so the hexagons are evenly spaced
        x += y * sin(pi / 6.0)
        y = y * cos(pi / 6.0)
        
        return (x, y)
    
    
    def _link(self, x, y, link):
        r"""Get the canvas coordinates of the beginning and end of a chip's link
        rectangle. The two coordinates are for the two sides of the link in clockwise
        order.
        
        
        Parameters
        ----------
        x : int
        y : int
            The chip whose link coordinates are required.
        link : :py:class:`rig.machine.Links`
            The link whose coordinates are needed.
        
        Returns
        -------
        (x0, y0, x1, y1)
            The coordinates of the extreme edges of the link on the specified
            chip. For example, when getting the south link::
            
                    /\
                   /  \
                  /N NE\
                 |      |
                 |W    E|
                 |      |.... y0
                  \SW S/.
                   \  / .
                    \/...... y1
                     .  .
                     .  .
                    x1  x0
        """
        # Center of chip
        x, y = self._chip(x, y)
        
        # The angle the link leaves the chip with respect to the X-axis (note
        # that 0 is east and the link values progress in counter-clockwise
        # order, hence the negation).
        angle = -link * (pi / 3.0)
        
        # Move to the center of the given edge
        offset = (0.5 * cos(pi / 6.0)) + (self.chip_style.get("line_width") /
                                          2.0)
        x += offset * cos(angle)
        y += offset * sin(angle)
        
        # Get positions of the two edges of the link
        x1 = x + (self._link_width / 2.0) * cos(angle - (pi / 2.0))
        y1 = y + (self._link_width / 2.0) * sin(angle - (pi / 2.0))
        x2 = x + (self._link_width / 2.0) * cos(angle + (pi / 2.0))
        y2 = y + (self._link_width / 2.0) * sin(angle + (pi / 2.0))
        
        return x1, y1, x2, y2
    
    def _link_net(self, x, y, link, net):
        """Get the canvas position of the end of the specified net in the
        specified link."""
        return self._link_net_positions[(x, y, link, net)]
    
    
    def _core(self, x, y, core):
        """Get the canvas coordinates of the specified core."""
        num_cores = len(self._core_map[(x % self.machine.width,
                                        y % self.machine.height)])
        
        cx, cy = self._chip(x, y)
        dx, dy = self._core_offset(num_cores, core)
        
        return cx + dx, cy + dy
    
    
    def _calculate_link_net_positions(self):
        """Compute the position of the ends of each net segment passing through
        each link.
        
        Initialises the _link_net_positions lookup used by ._link_net(). Note,
        this lookup also includes entries for chips just beyond the bounds of
        the system which correspond with wrap-around connections.
        """
        # A lookup {(x, y, link, net): (cx, cy), ...} giving the canvas
        # coordinates of the end of each segment of net in a link.
        self._link_net_positions = {}
        for x in range(-1, self.machine.width + 1):
            for y in range(-1, self.machine.height + 1):
                for link in Links:
                    if ((x % self.machine.width, y % self.machine.height, link)
                            in self.machine):
                        x1, y1, x2, y2 = self._link(x, y, link)
                        
                        # Get the set of nets which pass through this link
                        nets = self._link_nets[(x % self.machine.width,
                                                y % self.machine.height,
                                                link)]
                        
                        # Find the total width of this set of nets along with the offset of the
                        # net of interest.
                        nets_width = 0.0
                        net_offsets = []
                        for net in nets:
                            net_offsets.append((nets_width + net.weight) *
                                               self.net_weight_scale)
                            nets_width += net.weight * 2.0
                        nets_width *= self.net_weight_scale
                        
                        # Convert nets_width and net_offset to range 0.0 to 1.0 (ratio
                        # of link width)
                        nets_width /= self._link_width
                        for net, net_offset in zip(nets, net_offsets):
                            net_offset /= self._link_width
                            
                            # Center the nets within the link
                            net_offset += (1.0 - nets_width) / 2.0
                            
                            # Add to table
                            self._link_net_positions[(x, y, link, net)] = \
                                (x1 + ((x2 - x1) * net_offset),
                                 y1 + ((y2 - y1) * net_offset))
    
    @property
    def bbox(self):
        """The bounding box of the image (x1, y1, x2, y2).
        
        Can be used to determine the ideal aspect ratio of an output image.
        """
        # Calculate the size of the bounding box around the live chips in the
        # diagram
        points = [self._chip(x, y) for x, y in self.machine]
        
        x1 = min(x for x, y in points)
        x2 = max(x for x, y in points)
        y1 = min(y for x, y in points)
        y2 = max(y for x, y in points)
        
        # Expand to fit half a chip-to-chip gap plus the fade-out-distance on
        # all sides of the diagram.
        spacing = (((1.0 + self.chip_style.get("line_width")) / 2.0) +
                   self.chip_spacing)
        x1 -= spacing
        y1 -= spacing
        x2 += spacing
        y2 += spacing
        
        return x1, y1, x2, y2
    
    
    def _iter_unique_links(self):
        """An iterator over the set of links in the machine, only listing both
        link directions for wrap-around connections.
        
        This iterator essentially lists all the links which must be drawn in the
        image and thus lists wrap-around links from both ends.
        """
        for x, y in machine:
            for direction in Links:
                if (x, y, direction) in machine:
                    # Determine if the other chip is at the other end of
                    # a wrap-around.
                    dx, dy = direction.to_vector()
                    x2 = x + dx
                    y2 = y + dy
                    wraps_around = (x2, y2) not in machine
                    
                    destination_exists = (x2 % machine.width,
                                          y2 % machine.height) in machine
                    
                    # Don't list links from both ends (unless wrapping
                    # around) and don't list links to dead chips.
                    if (destination_exists and
                            (wraps_around or (x, y) < (x2, y2))):
                        yield (x, y, direction)
    
    
    def _draw_chip(self, ctx, x, y):
        """Draw a single chip in the machine."""
        with self.chip_style(ctx, (x, y)):
            cx, cy = self._chip(x, y)
            ctx.translate(cx, cy)
            
            # Draw the chip as a hexagon
            ctx.move_to(0, 0.5)
            for step in range(1, 6):
                ctx.line_to(0.5 * sin(step * pi / 3.0),
                            0.5 * cos(step * pi / 3.0))
            ctx.close_path()
    
    
    def _draw_link(self, ctx, x, y, link):
        """Draw a link between two chips."""
        with self.link_style(ctx, (x, y, link), no_fill_stroke=True) as style:
            # Get the positions of the end of the link
            ax1, ay1, ax2, ay2 = self._link(x, y, link)
            
            # Get the position of the opposite end of the link
            dx, dy = link.to_vector()
            x2, y2 = x + dx, y + dy
            bx1, by1, bx2, by2 = self._link(x2, y2, link.opposite)
            
            # Determine if the link is a wrap-around
            wraps_around = (x2, y2) not in machine
            
            # Draw link fill
            fill = style.get("fill")
            if fill:
                ctx.move_to(ax1, ay1)
                ctx.line_to(bx2, by2)
                ctx.line_to(bx1, by1)
                ctx.line_to(ax2, ay2)
                ctx.close_path()
                
                r, g, b, a = fill
                # Fade-out wrap-around links
                if wraps_around:
                    gradient = cairo.LinearGradient(ax1, ay1, bx2, by2)
                    gradient.add_color_stop_rgba(0.0, r, g, b, a)
                    gradient.add_color_stop_rgba(0.5, r, g, b, a)
                    gradient.add_color_stop_rgba(1.0, r, g, b, 0.0)
                    ctx.set_source(gradient)
                else:
                    ctx.set_source_rgba(r, g, b, a)
                ctx.fill()
            
            # Draw link boundaries
            stroke = style.get("stroke")
            line_width = style.get("line_width")
            if stroke and line_width:
                ctx.move_to(ax1, ay1)
                ctx.line_to(bx2, by2)
                
                ctx.move_to(ax2, ay2)
                ctx.line_to(bx1, by1)
            
                # Fade-out wrap-around links
                r, g, b, a = stroke
                if wraps_around:
                    gradient = cairo.LinearGradient(ax1, ay1, bx2, by2)
                    gradient.add_color_stop_rgba(0.0, r, g, b, a)
                    gradient.add_color_stop_rgba(0.5, r, g, b, 0.0)
                    ctx.set_source(gradient)
                else:
                    ctx.set_source_rgba(r, g, b, a)
                ctx.set_line_width(line_width)
                ctx.stroke()
    
    
    def _draw_core(self, ctx, x, y, core_num):
        """Draw the specified core."""
        cx, cy = self._core(x, y, core_num)
        
        with self.core_style(ctx, self._core_map[(x, y)][core_num]):
            ctx.arc(cx, cy, self._core_diameter / 2.0, 0.0, 2.0 * pi)
    
    
    def _draw_ratswire(self, sx, sy, sc, dx, dy, dc):
        """Add a wire between the specified cores to the current Cairo path.
        
        Does not stroke the path!
        """
        # Work out what route would be taken when wrap-around links are
        # available.
        if self.has_wrap_around_links:
            vx, vy, vz = shortest_torus_path((sx, sy, 0), (dx, dy, 0),
                                             machine.width, machine.height)
        else:
            vx, vy, vz = shortest_mesh_path((sx, sy, 0), (dx, dy, 0))
        
        # Convert to XY only
        vx -= vz
        vy -= vz
        
        wraps_x = not (0 <= sx + vx < machine.width)
        wraps_y = not (0 <= sy + vy < machine.height)
        
        # A list of ((x1, y1), (x2, y2)) tuples giving the set of lines to be drawn
        # for this net.
        lines = []
        
        if not self.has_wrap_around_links or (not wraps_x and not wraps_y):
            # Just draw a straight-line path
            lines.append((self._core(sx, sy, sc), self._core(dx, dy, dc)))
        else:
            # This link wraps around. We draw it in two parts
            
            # First we draw the part going off the edge
            lines.append((self._core(sx, sy, sc),
                          self._core(sx + vx, sy + vy, dc)))
            
            # Second we draw the part coming back in again
            lines.append((self._core(dx - vx, dy - vy, sc),
                          self._core(dx, dy, dc)))
        
        # Draw the set of lines defined above with a slight arc
        for (x1, y1), (x2, y2) in lines:
            if x1 != x2 or y1 != y2:
                # Add a curve to the wire
                
                # Find the midpoint
                mx = (x2 - x1) / 2.0
                my = (y2 - y1) / 2.0
                
                # Adjust to add a curve
                alpha = atan2(my, mx)
                alpha += pi / 2.0
                mx += x1 + self.ratsnest_arc_height * cos(alpha)
                my += y1 + self.ratsnest_arc_height * sin(alpha)
                
                mx1 = mx2 = mx
                my1 = my2 = my
            else:
                # Add a loop to this self-loop net
                alpha = self.ratsnest_loop_angle
                mx1 = x1 + self.ratsnest_loop_height * cos(-pi / 2.0 - alpha)
                my1 = y1 + self.ratsnest_loop_height * sin(-pi / 2.0 - alpha)
                
                mx2 = x2 + self.ratsnest_loop_height * cos(-pi / 2.0 + alpha)
                my2 = y2 + self.ratsnest_loop_height * sin(-pi / 2.0 + alpha)
            
            ctx.move_to(x1, y1)
            ctx.curve_to(mx1, my1, mx2, my2, x2, y2)
    
    
    def _draw_net_ratsnest(self, ctx, net):
        """Draw the ratsnest for a given net."""
        # Build a list of (x, y, p) tuples which give the sources and sinks for
        # each net.
        with self.net_style(ctx, net):
            # Set the line width based on the net's weight.
            # XXX: We don't compensate for the fact that multi-source nets
            # appear more times than they should.
            line_width = net.weight * self.net_weight_scale
            line_width = max(line_width, self.net_min_line_width)
            ctx.set_line_width(line_width)
            
            # Draw all-to-all ratsnest connections between every source core and
            # destination core pair.
            sx, sy = self.placements[net.source]
            for sc, v in iteritems(self._core_map[(sx, sy)]):
                if v == net.source:
                    for destination in net.sinks:
                        dx, dy = self.placements[destination]
                        for dc, v in iteritems(self._core_map[(dx, dy)]):
                            if v == destination:
                                self._draw_ratswire(sx, sy, sc, dx, dy, dc)
    
    def _draw_route(self, ctx, net, route, start_points):
        """Add the wires for a route defined by a RoutingTree to the current
        Cairo path.
        
        This method will draw a line from each of the canvas (cx, cy)
        coordainates in start_points to each of the children of the RoutingTree
        node. This process is carried out recursively.
        
        Note: This does not stroke the path!
        """
        x, y = route.chip
        for direction, child in route.children:
            if direction is None:
                # Routes to unspecified destinations are simply not drawn
                pass
            elif direction.is_core:
                # Routes to a core are drawn as is.
                cx2, cy2 = self._core(x, y, direction.core_num)
                for cx1, cy1 in start_points:
                    if cx1 == cx2 and cy1 == cy2:
                        # A self connection! Draw as a loop.
                        alpha = self.ratsnest_loop_angle
                        mx1 = cx1 + self.ratsnest_loop_height * cos(-pi / 2.0 - alpha)
                        my1 = cy1 + self.ratsnest_loop_height * sin(-pi / 2.0 - alpha)
                        
                        mx2 = cx2 + self.ratsnest_loop_height * cos(-pi / 2.0 + alpha)
                        my2 = cy2 + self.ratsnest_loop_height * sin(-pi / 2.0 + alpha)
                        
                        ctx.move_to(cx1, cy1)
                        ctx.curve_to(mx1, my1, mx2, my2, cx2, cy2)
                    else:
                        # A connection to a different core. Draw straight.
                        ctx.move_to(cx1, cy1)
                        ctx.line_to(cx2, cy2)
            else:
                # This child traverses a chip-to-chip link.
                link = Links(direction)
                
                # Work out which chip the link goes to
                dx, dy = link.to_vector()
                x2 = (x + dx) % self.machine.width
                y2 = (y + dy) % self.machine.height
                
                # Draw all routes leaving this chip (possibly heading off the
                # side of the system if this is a wrapping connection)
                for start_xy in start_points:
                    ctx.move_to(*start_xy)
                    ctx.line_to(*self._link_net(x, y, link, net))
                    ctx.line_to(*self._link_net(x + dx, y + dy, link.opposite,
                                                net))
                
                # If this is a wrap-around link, draw the link on the other side
                # of the system
                if x2 != x + dx or y2 != y + dy:
                    ctx.move_to(*self._link_net(x2 - dx, y2 - dy, link, net))
                    ctx.line_to(*self._link_net(x2, y2, link.opposite, net))
                
                # If the destination is another chip, recursively continue
                # drawing the routes from there!
                if isinstance(child, RoutingTree):
                    self._draw_route(ctx, net, child,
                                     [self._link_net(x2, y2, link.opposite,
                                                     net)])
    
    
    def _draw_net_route(self, ctx, net):
        """Draw the route of a net."""
        with self.net_style(ctx, net):
            route = self.routes[net]
            
            # Set the line width for the net proportional to its weight
            line_width = net.weight * self.net_weight_scale
            line_width = max(line_width, self.net_min_line_width)
            ctx.set_line_width(line_width)
            
            start_points = []
            sx, sy = self.placements[net.source]
            for sc, v in iteritems(self._core_map[(sx, sy)]):
                if v == net.source:
                    start_points.append(self._core(sx, sy, sc))
            
            self._draw_route(ctx, net, route, start_points)
    
    def _draw_wire_mask(self, ctx):
        """Draw the mask for wires within the system.
        
        This mask fades out wrap-around wires moving off the edge of the
        diagram.
        """
        with ctx:
            gradient = cairo.RadialGradient(0.0, 0.0,
                                            0.5 + (self.chip_spacing / 2.0),
                                            0.0, 0.0,
                                            0.5 + self.chip_spacing)
            gradient.add_color_stop_rgba(0.0, 0, 0, 0, 1.0)
            gradient.add_color_stop_rgba(1.0, 0, 0, 0, 0.0)
            
            # Around the edge of the system, fade out along the radius of the
            # chips at the edge.
            for x in range(machine.width):
                for y in [0, machine.height - 1]:
                    cx, cy = self._chip(x, y)
                    with ctx:
                        ctx.translate(cx, cy)
                        ctx.set_source(gradient)
                        ctx.paint()
            for y in range(1, machine.height - 1):
                for x in [0, machine.width - 1]:
                    cx, cy = self._chip(x, y)
                    with ctx:
                        ctx.translate(cx, cy)
                        ctx.set_source(gradient)
                        ctx.paint()
            
            # Within the bounds of the system, keep all wires
            ctx.move_to(*self._chip(0, 0))
            ctx.line_to(*self._chip(machine.width - 1, 0))
            ctx.line_to(*self._chip(machine.width - 1, machine.height - 1))
            ctx.line_to(*self._chip(0, machine.height - 1))
            ctx.close_path()
            ctx.set_source_rgba(0,0,0,1)
            ctx.fill()
    
    
    def draw(self, ctx, width, height):
        """Draw the diagram onto the supplied Cairo context, centered in a rectangle
        from 0, 0 at the given width and height."""
        with ctx:
            x1, y1, x2, y2 = self.bbox
            
            # Scale the drawing such that it fits the image perfectly.
            bbox_width = x2 - x1
            bbox_height = y2 - y1
            scale = min(width / bbox_width, height / bbox_height)
            ctx.scale(scale, scale)
            
            # Center the diagram in the allotted space
            x1 -= ((width / scale) - bbox_width) / 2.0
            y1 -= ((height / scale) - bbox_height) / 2.0
            ctx.translate(-x1, -y1)
            
            # Draw the chips
            for x, y in self.machine:
                self._draw_chip(ctx, x, y)
            
            # Draw the links between them
            for x, y, direction in self._iter_unique_links():
                self._draw_link(ctx, x, y, direction)
            
            # Draw nets. These are drawn into a group which will later be masked
            # to fade out wrap-around connections.
            ctx.push_group()
            if self.routes == {}:
                # Draw the ratsnest if no routes are supplied. Note it is drawn
                # in a group so that the whole thing can have its opacity
                # reduced.
                ctx.push_group()
                for net in self.nets:
                    self._draw_net_ratsnest(ctx, net)
                ctx.pop_group_to_source()
                ctx.paint_with_alpha(self.ratsnest_alpha)
            else:
                # Draw routed connections, if available
                for net in self.nets:
                    self._draw_net_route(ctx, net)
            net_surface = ctx.pop_group()
            
            # Mask off the nets going beyond the system boundary
            ctx.push_group()
            self._draw_wire_mask(ctx)
            net_mask = ctx.pop_group()
            
            ctx.set_source(net_surface)
            ctx.mask(net_mask)
            
            # Draw the cores
            for (x, y), vertices_on_chip in iteritems(self._core_map):
                for core_num in vertices_on_chip:
                    self._draw_core(ctx, x, y, core_num)
        


if __name__=="__main__":
    width = 2000
    height = 1600
    
    from rig.machine import Machine
    
    w, h = 96, 60
    w, h = 12, 12
    w, h = 3, 3
    w, h = 48, 24
    
    machine = Machine(w, h, chip_resources={Cores: 18})
    ## SpiNN-5
    #nominal_live_chips = set([  # noqa
    #                                    (4, 7), (5, 7), (6, 7), (7, 7),
    #                            (3, 6), (4, 6), (5, 6), (6, 6), (7, 6),
    #                    (2, 5), (3, 5), (4, 5), (5, 5), (6, 5), (7, 5),
    #            (1, 4), (2, 4), (3, 4), (4, 4), (5, 4), (6, 4), (7, 4),
    #    (0, 3), (1, 3), (2, 3), (3, 3), (4, 3), (5, 3), (6, 3), (7, 3),
    #    (0, 2), (1, 2), (2, 2), (3, 2), (4, 2), (5, 2), (6, 2),
    #    (0, 1), (1, 1), (2, 1), (3, 1), (4, 1), (5, 1),
    #    (0, 0), (1, 0), (2, 0), (3, 0), (4, 0),
    #])
    #machine.dead_chips = set((x, y)
    #                         for x in range(8)
    #                         for y in range(8)) - nominal_live_chips
    
    
    from collections import OrderedDict
    ideal_placement = OrderedDict(((x, y), object())
                                  for x in range(w)
                                  for y in range(h))
    vertices = list(itervalues(ideal_placement))
    vertices_resources = {v: {Cores: 2} for v in vertices}
    
    def i(x, y):
        #if x >= w or x < 0 or y >= h or y < 0:
        #    return None
        #else:
        return ideal_placement[(x%w, y%h)]
    nets = []
    
    # Nearest-neighbour connectivity
    nets += [Net(i(x, y),
                 [xy for xy in [i(x+1,y+1), # Top
                                i(x+0,y+1),
                                #i(x-1,y+1), # Left
                                i(x-1,y+0),
                                i(x-1,y-1), # Bottom
                                i(x+0,y-1),
                                #i(x+1,y-1), # Right
                                i(x+1,y+0),
                                i(x+0,y+0),  # Self-loop
                                ]
                  if xy is not None], weight=0.3 + random.random()*0.7)
             for x in range(w)
             for y in range(h)]
    
    # Self-loop connectivity
    #nets += [Net(v, v) for v in vertices]
    
    ## Random connectivity
    #fan_out = 1, 4
    #net_prob = 0.5
    #nets += [Net(v, random.sample(vertices, random.randint(*fan_out)),
    #             0.5 + random.random()*0.5)
    #         for v in vertices
    #         if random.random() < net_prob]
    
    # Thick pipeline connectivity
    #n_vertices = len(vertices)
    #thickness = 12
    #nets += [Net(vertices[i],
    #             vertices[(i//thickness + 1)*thickness: (i//thickness + 2)*thickness])
    #         for i in range(n_vertices)
    #         if i + thickness < n_vertices]
    
    ## Nengo-style pipeline of ensemble arrays
    #n_vertices = len(vertices)
    #thickness = 10
    #vertex_iter = iter(vertices)
    #last_node = None
    #try:
    #    while True:
    #        node = next(vertex_iter)
    #        if last_node is not None:
    #            nets.append(Net(last_node, node))
    #        
    #        ensemble_array = []
    #        try:
    #            for _ in range(thickness):
    #                ensemble_array.append(next(vertex_iter))
    #        except StopIteration:
    #            pass
    #        nets.append(Net(node, ensemble_array))
    #        
    #        last_node = next(vertex_iter)
    #        for v in ensemble_array:
    #            nets.append(Net(v, last_node))
    #except StopIteration:
    #    pass
    
    import logging
    logging.basicConfig(level=logging.DEBUG)
    
    constraints = [ReserveResourceConstraint(Cores, slice(2, 18))]
    #placements = rig.place_and_route.place(vertices_resources, nets,
    #                                       machine, constraints, effort=1)
    placements = {v: xy for xy, v in iteritems(ideal_placement)}
    allocations = rig.place_and_route.allocate(vertices_resources, nets,
                                               machine, constraints,
                                               placements)
    routes = rig.place_and_route.route(vertices_resources, nets,
                                       machine, constraints,
                                       placements, allocations, radius=0)
    
    import pickle
    with open("/tmp/placement.pickle", "rb") as f:
        data = pickle.load(f)
        
        machine = data["machine"]
        vertices_resources = data["vertices_resources"]
        nets = data["nets"]
        constraints = data["constraints"]
        placements = data["placements"]
        allocations = data["allocations"]
        routes = data["routes"]
    
    core_style = default_core_style.copy()
    for constraint in constraints:
        core_style.set(constraint, "fill", (0.2, 0.2, 0.2, 0.5))
        core_style.set(constraint, "stroke", None)
    
    d = Diagram(machine=machine,
                vertices_resources=vertices_resources,
                nets=nets,
                constraints=constraints,
                placements=placements,
                allocations=allocations,
                routes=routes,
                core_resource=Cores,
                core_style=core_style)
    
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32,
                                 width,
                                 height)
    ctx = cairo.Context(surface)
    d.draw(ctx, width, height)
    surface.write_to_png("/tmp/out.png")
