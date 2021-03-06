% Generated by roxygen2: do not edit by hand
% Please edit documentation in R/lipd.R
\name{lipd_write}
\alias{lipd_write}
\title{Saves current state of LiPD object data. Outputs to a LiPD file.
Steps: create tmp, create bag dir, get dsn, splice csv from json, write csv, clean json, write json, create bagit,
zip up bag folder, place lipd in target dst, move to original dir, delete tmp}
\usage{
lipd_write(j, path, dsn, ignore.warnings, removeNamesFromLists = FALSE)
}
\arguments{
\item{list}{j: Metadata}

\item{char}{path: Destination path}

\item{char}{dsn: Dataset name}
}
\value{
none:
}
\description{
Saves current state of LiPD object data. Outputs to a LiPD file.
Steps: create tmp, create bag dir, get dsn, splice csv from json, write csv, clean json, write json, create bagit,
zip up bag folder, place lipd in target dst, move to original dir, delete tmp
}
\keyword{internal}
