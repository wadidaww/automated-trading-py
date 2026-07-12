class MathFormula:
    @staticmethod
    def calc_percentage_change(old_value, new_value) -> float:
        """
        Calculate the percentage change between two values.

        :param old_value: The original value.
        :param new_value: The new value.
        :return: The percentage change as a float.
        """
        if old_value == 0:
            raise ValueError("Old value cannot be zero for percentage change calculation.")

        change = new_value - old_value
        percentage_change = (change / old_value) * 100
        return percentage_change

    @staticmethod
    def calc_z_score(value, mean, std_dev) -> float:
        """
        Calculate the z-score of a value.

        :param value: The value to calculate the z-score for.
        :param mean: The mean of the dataset.
        :param std_dev: The standard deviation of the dataset.
        :return: The z-score as a float.
        """
        if std_dev == 0:
            raise ValueError("Standard deviation cannot be zero for z-score calculation.")

        z_score = (value - mean) / std_dev
        return z_score

    @staticmethod
    def calc_std_dev(data) -> float:
        """
        Calculate the standard deviation of a dataset.

        :param data: A list of numerical values.
        :return: The standard deviation as a float.
        """
        if len(data) == 0:
            raise ValueError("Data list cannot be empty for standard deviation calculation.")

        mean = MathFormula.calc_mean(data)
        variance = sum((x - mean) ** 2 for x in data) / len(data)
        std_dev = variance**0.5
        return std_dev

    @staticmethod
    def calc_mean(data) -> float:
        """
        Calculate the mean of a dataset.

        :param data: A list of numerical values.
        :return: The mean as a float.
        """
        if len(data) == 0:
            raise ValueError("Data list cannot be empty for mean calculation.")

        mean = sum(data) / len(data)
        return mean
