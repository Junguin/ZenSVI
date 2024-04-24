from pathlib import Path
import pandas as pd
from typing import Union
import h3
import osmnx as ox
import numpy as np
from shapely.geometry import LineString, Polygon
import geopandas as gpd
from astral import LocationInfo, sun
import pytz
import datetime
from concurrent.futures import ProcessPoolExecutor 
from tqdm.auto import tqdm
from timezonefinder import TimezoneFinder

tf_instance = None  # Global variable to store the TimezoneFinder instance


def init_timezone_finder():
    global tf_instance
    tf_instance = TimezoneFinder()


def _calculate_angle(line):
    if isinstance(line, LineString):
        start, end = list(line.coords)[0], list(line.coords)[-1]
        angle = np.arctan2(end[1] - start[1], end[0] - start[0])
        return np.degrees(angle) % 360
    else:
        return None


def _create_hexagon(gdf, resolution=7):
    gdf = gdf.to_crs(4326)
    gdf["h3_id"] = gdf.apply(_lat_lng_to_h3, resolution=resolution, axis=1)
    hex_gdf = gdf["h3_id"].drop_duplicates().apply(_h3_to_polygon)
    hex_gdf = gpd.GeoDataFrame(geometry=hex_gdf, crs=4326)
    return hex_gdf


def _lat_lng_to_h3(row, resolution=7):
    """Convert latitude and longitude to H3 hex ID at the specified resolution."""
    return h3.geo_to_h3(row["lat"], row["lon"], resolution)


def _h3_to_polygon(hex_id):
    """Convert H3 hex ID to a Shapely polygon."""
    vertices = h3.h3_to_geo_boundary(hex_id, geo_json=True)
    return Polygon(vertices)


def _day_or_night(series, date_time):
    # Set up the location
    location = LocationInfo(latitude=series["lat"], longitude=series["lon"])

    # Get sunrise and sunset times for the given date
    s = sun.sun(location.observer, date=date_time.date(), tzinfo=date_time.tzinfo)

    sunrise = s["sunrise"]
    sunset = s["sunset"]

    # Determine if it's daytime or nighttime
    if sunrise <= date_time <= sunset:
        return "daytime"
    else:
        return "nighttime"


def _get_timezone_str(lat, lon):
    global tf_instance
    return tf_instance.timezone_at(lng=lon, lat=lat)


def _get_local_datetime(timestamp, timezone_str):
    dt = datetime.datetime.fromtimestamp(timestamp / 1000)
    dt2 = dt.astimezone(pytz.timezone(timezone_str))
    return dt2


def _process_row(row):
    timezone_str = _get_timezone_str(row["lat"], row["lon"])
    local_datetime = _get_local_datetime(row["captured_at"], timezone_str)
    return timezone_str, local_datetime


class MLYMetadata:
    """A class to compute metadata for the MLY dataset.
    Args:
        path_input (Union[str, Path]): The path to the input CSV file (e.g., "mly_pids.csv"). The CSV file should contain the following columns: "id", "lat", "lon", "captured_at", "compass_angle", "creator_id", "sequence_id", "organization_id", "is_pano".
    """
    def __init__(self, path_input: Union[str, Path]):
        self.path_input = Path(path_input)
        self.df = pd.read_csv(self.path_input)
        with ProcessPoolExecutor(initializer=init_timezone_finder) as executor:
            results = list(
                tqdm(
                    executor.map(_process_row, self.df.to_dict("records")),
                    total=len(self.df),
                    desc="Computing timezone and local datetime",
                )
            )
        self.df["timezone"], self.df["local_datetime"] = zip(*results)

        self.metadata = None
        # get street network in the extent of the dataset with OSMnx
        self.street_network = ox.graph_from_bbox(
            bbox=(
                self.df["lat"].min(),
                self.df["lat"].max(),
                self.df["lon"].min(),
                self.df["lon"].max(),
            ),
            network_type="all",
        )
        self.street_network = ox.convert.graph_to_gdfs(self.street_network, nodes=False)
        self.street_network = ox.projection.project_gdf(self.street_network)
        self.gdf = gpd.GeoDataFrame(
            self.df,
            geometry=gpd.points_from_xy(self.df["lon"], self.df["lat"]),
            crs=4326,
        )
        self.gdf = self.gdf.to_crs(crs=self.street_network.crs)
        # calculate angle of the street segments
        self.street_network["angle"] = self.street_network["geometry"].apply(
            _calculate_angle
        )

        # create a dictionary of functions to compute metadata for each indicator at the image level
        self.indicator_metadata_image = {
            "year": self._compute_year_metadata_image,
            "month": self._compute_month_metadata_image,
            "day": self._compute_day_metadata_image,
            "hour": self._compute_hour_metadata_image,
            "day_of_week": self._compute_day_of_week_metadata_image,
            "daytime_nighttime": self._compute_daytime_nighttime_metadata_image,
            "relative_angle": self._compute_relative_angle_metadata_image,
        }

        # create a dictionary of functions to compute metadata for each indicator at the grid level
        self.indicator_metadata_grid_street = {
            "coverage": self._compute_coverage_metadata_grid_street,
            "count": self._compute_count_metadata_grid_street,
            "days_elapsed": self._compute_days_elapsed_metadata_grid_street,
            "most_recent_date": self._compute_most_recent_date_metadata_grid_street,
            "oldest_date": self._compute_oldest_date_metadata_grid_street,
            "number_of_years": self._compute_number_of_years_metadata_grid_street,
            "number_of_months": self._compute_number_of_months_metadata_grid_street,
            "number_of_days": self._compute_number_of_days_metadata_grid_street,
            "number_of_hours": self._compute_number_of_hours_metadata_grid_street,
            "number_of_days_of_week": self._compute_number_of_days_of_week_metadata_grid_street,
            "number_of_daytime": self._compute_number_of_daytime_metadata_grid_street,
            "number_of_nighttime": self._compute_number_of_nighttime_metadata_grid_street,
            "average_compass_angle": self._compute_average_compass_angle_metadata_grid_street,
            "average_relative_angle": self._compute_average_relative_angle_metadata_grid_street,
            "average_is_pano": self._compute_average_is_pano_metadata_grid_street,
            "number_of_users": self._compute_number_of_users_metadata_grid_street,
            "number_of_sequences": self._compute_number_of_sequences_metadata_grid_street,
            "number_of_organizations": self._compute_number_of_organizations_metadata_grid_street,
        }

    def _compute_year_metadata_image(self):
        self.metadata["year"] = pd.to_datetime(
            self.df["local_datetime"]
        ).dt.year.astype(int)

    def _compute_month_metadata_image(self):
        self.metadata["month"] = pd.to_datetime(
            self.df["local_datetime"]
        ).dt.month.astype(int)

    def _compute_day_metadata_image(self):
        self.metadata["day"] = pd.to_datetime(self.df["local_datetime"]).dt.day.astype(
            int
        )

    def _compute_hour_metadata_image(self):
        self.metadata["hour"] = pd.to_datetime(
            self.df["local_datetime"]
        ).dt.hour.astype(int)

    def _compute_day_of_week_metadata_image(self):
        self.metadata["day_of_week"] = pd.to_datetime(
            self.df["local_datetime"]
        ).dt.dayofweek.astype(int)

    def _compute_daytime_nighttime_metadata_image(self):
        self.metadata["daytime_nighttime"] = self.df.apply(
            lambda x: _day_or_night(x, pd.to_datetime(x["local_datetime"])), axis=1
        )

    def _compute_relative_angle_metadata_image(self):
        nearest_line = self.nearest_line[["id", "relative_angle"]]
        self.metadata = self.metadata.merge(nearest_line, on="id", how="left")

    def _compute_coverage_metadata_grid_street(self):
        # calculate the area covered by buffer (self.coverage_buffer) from each point (self.gdf) in gdf
        buffer = self.gdf.buffer(self.coverage_buffer).unary_union
        # if self.metadata is line, calculate the coverage as the ratio of length of the line within the buffer over the total length.
        # Otherwise, calculate the ratio of area of the polygon within the buffer over the total area.
        if isinstance(self.metadata["geometry"].iloc[0], LineString):
            self.metadata["coverage"] = (
                self.metadata["geometry"].intersection(buffer).length
                / self.metadata["geometry"].length
            )
        else:
            self.metadata["coverage"] = (
                self.metadata["geometry"].intersection(buffer).area
                / self.metadata["geometry"].area
            )

    def _compute_count_metadata_grid_street(self):
        self.metadata["count"] = self.joined.groupby(self.columns_to_group_by).size()

    def _compute_days_elapsed_metadata_grid_street(self):
        # number of days between the most recent and oldest point (self.gdf) within self.metadata
        # calculate the time elapsed
        grouped = self.joined.groupby(self.columns_to_group_by)["local_datetime"]
        time_diff = pd.to_datetime(grouped.max()) - pd.to_datetime(grouped.min())

        # Convert the time difference to days
        days_diff = time_diff.dt.days
        self.metadata["days_elapsed"] = days_diff

    def _compute_most_recent_date_metadata_grid_street(self):
        # number of days between the most recent point (self.gdf) and the most recent point within self.metadata
        # calculate the age of the most recent point
        self.metadata["most_recent_date"] = (
            pd.to_datetime(
                self.joined.groupby(self.columns_to_group_by)["local_datetime"].max(),
                utc=True,
            )
        ).dt.date.astype(str)

    def _compute_oldest_date_metadata_grid_street(self):
        # number of days between the oldest point (self.gdf) and the oldest point within self.metadata
        # calculate the age of the oldest point
        self.metadata["oldest_date"] = (
            pd.to_datetime(
                self.joined.groupby(self.columns_to_group_by)["local_datetime"].min(),
                utc=True,
            )
        ).dt.date.astype(str)

    def _compute_number_of_years_metadata_grid_street(self):
        # number of unique years in the dataset
        # spaital join self.gdf to self.metadata
        joined = self.joined
        joined["year"] = pd.to_datetime(joined["local_datetime"]).dt.year
        # calculate the number of unique years
        self.metadata["number_of_years"] = joined.groupby(self.columns_to_group_by)[
            "year"
        ].nunique()

    def _compute_number_of_months_metadata_grid_street(self):
        # number of unique months in the dataset
        # spaital join self.gdf to self.metadata
        joined = self.joined
        joined["month"] = pd.to_datetime(joined["local_datetime"]).dt.month
        # calculate the number of unique months
        self.metadata["number_of_months"] = joined.groupby(self.columns_to_group_by)[
            "month"
        ].nunique()

    def _compute_number_of_days_metadata_grid_street(self):
        # number of unique days in the dataset
        # spaital join self.gdf to self.metadata
        joined = self.joined
        joined["day"] = pd.to_datetime(joined["local_datetime"]).dt.day
        # calculate the number of unique days
        self.metadata["number_of_days"] = joined.groupby(self.columns_to_group_by)[
            "day"
        ].nunique()

    def _compute_number_of_hours_metadata_grid_street(self):
        # number of unique hours in the dataset
        # spaital join self.gdf to self.metadata
        joined = self.joined
        joined["hour"] = pd.to_datetime(joined["local_datetime"]).dt.hour
        # calculate the number of unique hours
        self.metadata["number_of_hours"] = joined.groupby(self.columns_to_group_by)[
            "hour"
        ].nunique()

    def _compute_number_of_days_of_week_metadata_grid_street(self):
        # number of unique days of week in the dataset
        # spaital join self.gdf to self.metadata
        joined = self.joined
        joined["day_of_week"] = pd.to_datetime(joined["local_datetime"]).dt.dayofweek
        # calculate the number of unique days of week
        self.metadata["number_of_days_of_week"] = joined.groupby(
            self.columns_to_group_by
        )["day_of_week"].nunique()

    def _compute_number_of_daytime_metadata_grid_street(self):
        # number of points captured during daytime
        # calculate the number of points captured during daytime using _day_or_night function
        joined = self.joined.copy()
        joined["daytime"] = self.joined.apply(
            lambda x: _day_or_night(x, pd.to_datetime(x["local_datetime"])),
            axis=1,
        ).eq("daytime")
        # group by self.columns_to_group_by and count the number of points captured during daytime
        joined = joined.groupby(self.columns_to_group_by)["daytime"].sum()
        self.metadata["number_of_daytime"] = joined

    def _compute_number_of_nighttime_metadata_grid_street(self):
        # number of points captured during nighttime
        # calculate the number of points captured during nighttime using _day_or_night function
        joined = self.joined.copy()
        joined["nighttime"] = self.joined.apply(
            lambda x: _day_or_night(x, pd.to_datetime(x["local_datetime"])),
            axis=1,
        ).eq("nighttime")
        # group by self.columns_to_group_by and count the number of points captured during nighttime
        joined = joined.groupby(self.columns_to_group_by)["nighttime"].sum()
        self.metadata["number_of_nighttime"] = joined

    def _compute_average_compass_angle_metadata_grid_street(self):
        # average compass angle of the points within self.metadata
        # calculate the average compass angle
        self.metadata["average_compass_angle"] = self.joined.groupby(
            self.columns_to_group_by
        )["compass_angle"].mean()

    def _compute_average_relative_angle_metadata_grid_street(self):
        # average relative angle of the points within self.metadata
        joined = self.joined.merge(self.nearest_line, on="id", how="left")
        # calculate the average relative angle
        self.metadata["average_relative_angle"] = joined.groupby(
            self.columns_to_group_by
        )["relative_angle"].mean()

    def _compute_average_is_pano_metadata_grid_street(self):
        # average is_pano of the points within self.metadata
        # calculate the average is_pano
        self.metadata["average_is_pano"] = self.joined.groupby(
            self.columns_to_group_by
        )["is_pano"].mean()

    def _compute_number_of_users_metadata_grid_street(self):
        # number of unique users in the dataset
        # calculate the number of unique users
        self.metadata["number_of_users"] = self.joined.groupby(
            self.columns_to_group_by
        )["creator_id"].nunique()

    def _compute_number_of_sequences_metadata_grid_street(self):
        # number of unique sequences in the dataset
        # calculate the number of unique sequences
        self.metadata["number_of_sequences"] = self.joined.groupby(
            self.columns_to_group_by
        )["sequence_id"].nunique()

    def _compute_number_of_organizations_metadata_grid_street(self):
        # number of unique organizations in the dataset
        # calculate the number of unique organizations
        self.metadata["number_of_organizations"] = self.joined.groupby(
            self.columns_to_group_by
        )["organization_id"].nunique()

    def _compute_image_metadata(self, indicator_list):
        # define self.metadata as a copy of the input DataFrame with only "id" column
        self.metadata = self.df.copy()
        if indicator_list == "all":
            indicator_list = [
                "year",
                "month",
                "day",
                "hour",
                "day_of_week",
                "daytime_nighttime",
                "relative_angle",
            ]
        else:
            # split string of indicators into a list
            indicator_list = indicator_list.split(" ")
        for indicator in indicator_list:
            self.indicator_metadata_image[indicator]()
        return self.metadata

    def _compute_grid_metadata(self, grid_resolution, indicator_list):
        if indicator_list == "all":
            indicator_list = [
                "coverage",
                "count",
                "days_elapsed",
                "most_recent_date",
                "oldest_date",
                "number_of_years",
                "number_of_months",
                "number_of_days",
                "number_of_hours",
                "number_of_days_of_week",
                "number_of_daytime",
                "number_of_nighttime",
                "average_compass_angle",
                "average_relative_angle",
                "average_is_pano",
                "number_of_users",
                "number_of_sequences",
                "number_of_organizations",
            ]
        else:
            # split string of indicators into a list
            indicator_list = indicator_list.split(" ")
        for indicator in indicator_list:
            self.indicator_metadata_grid_street[indicator]()
        return self.metadata

    def _compute_street_metadata(self, indicator_list):
        if indicator_list == "all":
            indicator_list = [
                "coverage",
                "count",
                "days_elapsed",
                "most_recent_date",
                "oldest_date",
                "number_of_years",
                "number_of_months",
                "number_of_days",
                "number_of_hours",
                "number_of_days_of_week",
                "number_of_daytime",
                "number_of_nighttime",
                "average_compass_angle",
                "average_relative_angle",
                "average_is_pano",
                "number_of_users",
                "number_of_sequences",
                "number_of_organizations",
            ]
        else:
            # split string of indicators into a list
            indicator_list = indicator_list.split(" ")
        for indicator in indicator_list:
            self.indicator_metadata_grid_street[indicator]()
        return self.metadata

    def compute_metadata(
        self,
        unit: str = "image",
        grid_resolution: int = 7,
        coverage_buffer: int = 50,
        indicator_list: str = "all",
        path_output: Union[str, Path] = None,
    ):
        """
        Compute metadata for the dataset.

        Args:
            unit (str): The unit of analysis. Defaults to "image".
            grid_resolution (int): The resolution of the H3 grid. Defaults to 7.
            indicator_list (str): List of indicators to compute metadata for. Defaults to "all". Use space-separated string of indicators or "all". Options for image-level metadata: "year", "month", "day", "hour", "day_of_week", "relative_angle". Options for grid-level metadata: "coverage", "count", "days_elapsed", "most_recent_date", "oldest_date", "number_of_years", "number_of_months", "number_of_days", "number_of_hours", "number_of_days_of_week", "number_of_daytime", "number_of_nighttime", "average_compass_angle", "average_relative_angle", "average_is_pano", "number_of_users", "number_of_sequences", "number_of_organizations". Defaults to "all".
            path_output (Union[str, Path], optional): Path to save the output metadata. Defaults to None.

        Returns:
            pd.DataFrame: A DataFrame containing the computed metadata.
        """
        # set coverage buffer as a class attribute
        self.coverage_buffer = coverage_buffer
        # check indicator_list and pre-compute metadata, e.g., relative_angle
        if "all" in indicator_list or "relative_angle" in indicator_list:
            # find the nearest street segment for each point with gdf sjoin_nearest
            nearest_line = self.gdf.sjoin_nearest(self.street_network, how="left")
            # calculate the relative angle between the street segment and the point. It should be 0-90 degrees if the point is on the right side of the street segment, 90-180 degrees if the point is on the left side of the street segment, and 180-360 degrees if the point is behind the street segment.
            nearest_line["relative_angle"] = (
                nearest_line["angle"] - nearest_line["compass_angle"]
            ).abs()
            # group by id and get the nearest line with the minimum relative angle
            nearest_line = (
                nearest_line.groupby("id")["relative_angle"].min().reset_index()
            )
            nearest_line = nearest_line[["id", "relative_angle"]]
            nearest_line = nearest_line[["id", "relative_angle"]].merge(
                self.gdf, on="id", how="right"
            )
            self.nearest_line = gpd.GeoDataFrame(nearest_line)

        if unit == "image":
            df = self._compute_image_metadata(indicator_list)
        elif unit == "grid":
            self.metadata = _create_hexagon(self.gdf, resolution=grid_resolution)
            # reproject self.metadata to the same crs as self.street_network
            self.metadata = self.metadata.to_crs(self.street_network.crs)
            # spaital join self.gdf to self.metadata
            self.joined = self.gdf.sjoin(self.metadata)
            # Get a list of all columns that contain 'index_right'
            self.columns_to_group_by = "index_right"
            df = self._compute_grid_metadata(grid_resolution, indicator_list)
        elif unit == "street":
            self.metadata = self.street_network.copy()
            # only keep index and geometry columns
            self.metadata = self.metadata[["geometry"]]
            # spaital join self.gdf to self.metadata
            self.joined = self.gdf.sjoin_nearest(self.metadata)
            self.columns_to_group_by = [
                col for col in self.joined.columns if "index_right" in col
            ]
            df = self._compute_street_metadata(indicator_list)
        else:
            raise ValueError("Invalid unit of analysis provided.")
        if path_output:
            if unit == "image":
                df.to_csv(path_output, index=False)
            else:
                if Path(path_output).suffix == ".geojson":
                    df.to_file(path_output, driver="GeoJSON")
                elif Path(path_output).suffix == ".shp":
                    df.to_file(path_output)
                elif Path(path_output).suffix == ".csv":
                    df = pd.DataFrame(df.drop(columns="geometry"))
                    df.to_csv(path_output, index=False)
                else:
                    raise ValueError(
                        "Invalid file format provided. Please provide a .csv, .shp, or .geojson file."
                    )
        return df
