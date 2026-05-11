# Classes to read BuildStock data from OEDI using polars
import polars as pl
import fsspec
from typing import List, Tuple, Optional
from pathlib import Path

class OEDIParquetReader:
    """
    .. deprecated::
        MARKED FOR REMOVAL: This class is not imported or used anywhere in the
        application. All parquet reading is handled directly in
        ``BuildStockDataManager``.

    A utility class to read specific columns from remote Parquet files hosted on platforms like OEDI.
    Uses Polars for efficient data handling and processing.

    Attributes:
        fs: A filesystem interface from fsspec for handling remote files
    """

    def __init__(self, filesystem: str = "https") -> None:
        """
        Initialize the OEDIParquetReader.

        Args:
            filesystem (str): Filesystem type ("https" for HTTP URIs, "s3" for AWS S3 URIs). 
                            Default is "https".
        """
        self.fs = fsspec.filesystem(filesystem)
    
    def read_columns(self, parquet_uri: str, column_list: List[str]) -> Tuple[pl.DataFrame, List[str]]:
        """
        Read specified columns from a remote Parquet file.

        Args:
            parquet_uri (str): URI to the remote Parquet file.
            column_list (List[str]): List of column names to read.

        Returns:
            Tuple[pl.DataFrame, List[str]]: A DataFrame with requested columns and a list of 
                                          missing columns.

        Raises:
            Exception: If there's an error reading the Parquet file.
            ValueError: If none of the requested columns are found in the file.
        """
        try:
            # First read just the schema using scan_parquet
            schema = pl.scan_parquet(parquet_uri).collect_schema()
            column_names = schema.keys()

            # Identify valid and missing columns
            missing_columns = [col for col in column_list if col not in column_names]
            valid_columns = [col for col in column_list if col in column_names]

            if not valid_columns:
                raise ValueError("None of the requested columns were found in the Parquet file.")
            
            # Read only the valid columns using scan_parquet for memory efficiency
            df = pl.scan_parquet(parquet_uri).select(valid_columns).collect()
            
            # Add index column to maintain parity with pandas version
            df = df.with_row_index("index")

            return df, missing_columns

        except ValueError:
            raise
        except Exception as e:
            raise Exception(f"Error reading Parquet file: {e}")

    def get_available_columns(self, parquet_uri: str) -> List[str]:
        """
        Retrieve all available columns from a remote Parquet file.

        Args:
            parquet_uri (str): URI to the remote Parquet file.

        Returns:
            List[str]: List of column names available in the Parquet file.

        Raises:
            Exception: If there's an error fetching column names.
        """
        try:
            # Use scan_parquet to efficiently read just the schema
            schema = pl.scan_parquet(parquet_uri).collect_schema()
            return list(schema.keys())
        except Exception as e:
            raise Exception(f"Error fetching column names: {e}")

    def validate_parquet_file(self, parquet_uri: str) -> bool:
        """
        Validate that a Parquet file exists and is readable.

        Args:
            parquet_uri (str): URI to the remote Parquet file.

        Returns:
            bool: True if file is valid and readable, False otherwise.
        """
        try:
            _ = pl.scan_parquet(parquet_uri).collect_schema()
            return True
        except Exception:
            return False
